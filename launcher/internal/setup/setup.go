// Package setup installs Case File Manager on Windows when someone
// double-clicks Setup.exe. It replaces the old PowerShell script: it asks
// Windows for administrator rights, registers the services, sets folder
// permissions and firewall rules, then opens the Control Center already
// signed in with the one-time setup code. The Control Center installs
// everything else (Python, Tesseract OCR, Ollama and the embedding model).
//
// Every command it runs is fixed here; nothing comes from the browser.
package setup

import (
	"encoding/json"
	"fmt"
	"io"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

// Options are Setup.exe's settings. The defaults suit a standard install.
type Options struct {
	InstallDir string
	Port       int // Control Center, loopback only
	AppPort    int // web app on the LAN
	// NoBrowser skips opening the Control Center (automated tests).
	NoBrowser bool
	// Pause waits before closing the window, so a double-click shows the
	// result instead of flashing past.
	Pause bool
	// Handoff is a file to write the Control Center address to instead of
	// opening it. The unelevated Setup.exe that asked for administrator
	// rights opens it, so the browser doesn't run as administrator.
	Handoff string
	Out     io.Writer
}

// Defaults returns the options for a standard install.
func Defaults() Options {
	return Options{InstallDir: `C:\CaseFiles`, Port: 8443, AppPort: 443}
}

// LauncherService is the launcher's Windows service name.
const LauncherService = "CaseFileManagerLauncher"

// layout is where everything goes under the install folder.
type layout struct {
	root, runtime, app, data, logs, certs, state string
}

func newLayout(root string) layout {
	j := func(p string) string { return filepath.Join(root, p) }
	return layout{root: root, runtime: j("runtime"), app: j("app"), data: j("data"), logs: j("logs"), certs: j("certs"), state: j("launcher")}
}

func (l layout) python() string     { return filepath.Join(l.runtime, "python", "python.exe") }
func (l layout) exe() string        { return filepath.Join(l.root, "launcher.exe") }
func (l layout) configPath() string { return filepath.Join(l.root, "launcher.json") }

// dirs are created before anything is copied.
func (l layout) dirs() []string {
	return []string{l.root, l.runtime, l.data, filepath.Join(l.data, "models"), filepath.Join(l.data, ".run"), l.logs, l.certs, l.state}
}

// mergeConfig writes the install's paths and ports into launcher.json and
// keeps any other settings from an earlier install (such as hostname).
func mergeConfig(existing []byte, l layout, o Options) ([]byte, error) {
	cfg := map[string]any{}
	existing = []byte(strings.TrimPrefix(string(existing), "\xef\xbb\xbf"))
	if len(strings.TrimSpace(string(existing))) > 0 {
		if err := json.Unmarshal(existing, &cfg); err != nil {
			return nil, fmt.Errorf("launcher.json isn't valid: %w", err)
		}
	}
	for k, v := range map[string]any{
		"data_dir": l.data, "state_dir": l.state, "certs_dir": l.certs, "app_dir": l.app,
		"runtime_dir": l.runtime, "log_dir": l.logs, "supervisor": "windows",
		"port": o.Port, "app_port": o.AppPort,
	} {
		cfg[k] = v
	}
	return json.MarshalIndent(cfg, "", "  ")
}

const (
	admins = "*S-1-5-32-544"
	system = "*S-1-5-18"
)

func account(id catalog.ServiceID) string {
	return `NT SERVICE\` + catalog.Service{ID: id}.WindowsName()
}

// aclRule is one icacls change. Exact rules reset the folder and keep only
// the listed grants, with nothing inherited from above.
type aclRule struct {
	Path   string
	Exact  bool
	Grants []string
}

// aclPlan gives each service only the files it needs. The launcher's
// credentials, audit log, downloads and the certificate authority stay
// Administrators and SYSTEM only.
func aclPlan(l layout) []aclRule {
	full := []string{admins + ":(OI)(CI)F", system + ":(OI)(CI)F"}
	with := func(extra ...string) []string { return append(append([]string{}, full...), extra...) }
	var readApp []string
	for _, s := range catalog.Services {
		readApp = append(readApp, account(s.ID)+":(OI)(CI)RX")
	}
	rules := []aclRule{
		// The services can read and run the app and runtimes.
		{Path: l.root, Exact: true, Grants: with(readApp...)},
		{Path: l.state, Exact: true, Grants: full},
		{Path: l.certs, Exact: true, Grants: full},
		// The web app may read its own certificate and key, never the CA key.
		{Path: filepath.Join(l.certs, "server.crt"), Grants: []string{account(catalog.API) + ":R"}},
		{Path: filepath.Join(l.certs, "server.key"), Grants: []string{account(catalog.API) + ":R"}},
		// Case data: the web app and worker. The models service only gets its folder.
		{Path: l.data, Exact: true, Grants: with(account(catalog.API)+":(OI)(CI)M", account(catalog.Worker)+":(OI)(CI)M")},
		{Path: filepath.Join(l.data, "models"), Grants: []string{account(catalog.Models) + ":(OI)(CI)M"}},
		// Logs: each service writes only its own folder.
		{Path: l.logs, Exact: true, Grants: full},
	}
	for _, s := range catalog.Services {
		rules = append(rules, aclRule{Path: filepath.Join(l.logs, string(s.ID)), Grants: []string{account(s.ID) + ":(OI)(CI)M"}})
	}
	return rules
}

// icaclsArgs turns a rule into icacls command lines.
func icaclsArgs(r aclRule) [][]string {
	if !r.Exact {
		return [][]string{append(append([]string{r.Path, "/grant"}, r.Grants...), "/C", "/Q")}
	}
	return [][]string{
		{r.Path, "/reset", "/T", "/C", "/Q"},
		append(append([]string{r.Path, "/inheritance:r", "/grant:r"}, r.Grants...), "/C", "/Q"),
	}
}

// Firewall rule names. Setup deletes and re-adds them, so running it again
// repairs them.
const (
	ruleWebApp = "Case File Manager web app"
	ruleWorker = "Case File Manager worker: no internet"
)

// firewallRules returns netsh command lines (after "netsh advfirewall
// firewall"). netsh wants name="..." quoted inside the argument, so these
// are raw command-line tails rather than argument lists.
func firewallRules(l layout, appPort int) []string {
	return []string{
		// The web app port, on Private networks only.
		fmt.Sprintf(`add rule name="%s" dir=in action=allow protocol=TCP localport=%d program="%s" profile=private`, ruleWebApp, appPort, l.python()),
		// The worker reads every case file, so it never gets to the internet.
		// It can still reach the local AI models on loopback.
		fmt.Sprintf(`add rule name="%s" dir=out action=block service=%s remoteip=0.0.0.0-126.255.255.255,128.0.0.0-255.255.255.255,::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff`,
			ruleWorker, catalog.Service{ID: catalog.Worker}.WindowsName()),
	}
}

func firewallDeletes() []string {
	return []string{`delete rule name="` + ruleWebApp + `"`, `delete rule name="` + ruleWorker + `"`}
}

var codeRE = regexp.MustCompile(`(?m)^[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}\s*$`)

// setupCode finds the code in setup-code.txt's text.
func setupCode(text string) string { return strings.TrimSpace(codeRE.FindString(text)) }

// controlCenterURL is the address Setup opens. The setup code rides in the
// fragment, which the browser never sends to a server; the page reads it,
// removes it from the address bar and signs in with it.
func controlCenterURL(port int, code string) string {
	u := fmt.Sprintf("https://localhost:%d/", port)
	if code != "" {
		u += "#setup=" + code
	}
	return u
}

// uninstallCmd is a double-clickable uninstaller left in the install folder.
const uninstallCmd = "@echo off\r\n\"%~dp0launcher.exe\" uninstall\r\n"

func shortcut(port int) string {
	return fmt.Sprintf("[InternetShortcut]\r\nURL=https://localhost:%d/\r\n", port)
}

const shortcutName = "Case File Manager Control Center.url"
