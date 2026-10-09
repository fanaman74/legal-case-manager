//go:build windows

package setup

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"

	"github.com/fanaman74/legal-case-manager/launcher/internal/winsvc"
)

const steps = 8

// Error is a setup failure with a plain next step.
type Error struct{ What, Next string }

func (e *Error) Error() string { return e.What + " " + e.Next }

func fail(what, next string) error { return &Error{What: what, Next: next} }

const againNext = "Double-click Setup.exe again."

type runner struct {
	o Options
	l layout
}

func (r *runner) step(n int, text string) { fmt.Fprintf(r.o.Out, "\n[%d/%d] %s\n", n, steps, text) }
func (r *runner) say(format string, a ...any) {
	fmt.Fprintf(r.o.Out, format+"\n", a...)
}

// Elevated reports whether this process has administrator rights.
func Elevated() bool { return windows.GetCurrentProcessToken().IsElevated() }

// DoubleClick is what happens when someone double-clicks Setup.exe (or
// launcher.exe). It asks Windows for administrator rights, installs, then
// opens the Control Center as the signed-in user, not as administrator.
func DoubleClick() error {
	o := Defaults()
	o.Out = os.Stdout
	o.Pause = true
	if Elevated() {
		return Run(o)
	}
	fmt.Println("Case File Manager setup")
	fmt.Println()
	fmt.Println("Windows will ask for permission to make changes. Choose Yes to continue.")
	handoff := filepath.Join(os.TempDir(), fmt.Sprintf("casefiles-setup-%d.txt", os.Getpid()))
	defer os.Remove(handoff)
	code, err := RunElevated([]string{"setup", "--pause", "--handoff", handoff})
	if err != nil {
		fmt.Println()
		fmt.Println(plain(err))
		pause(os.Stdin, os.Stdout)
		return nil
	}
	if code != 0 {
		// The elevated window already showed what went wrong.
		return nil
	}
	if b, err := os.ReadFile(handoff); err == nil {
		OpenBrowser(strings.TrimSpace(string(b)))
	}
	return nil
}

func plain(err error) string {
	var e *Error
	if errors.As(err, &e) {
		return "Setup stopped: " + e.What + "\nNext step: " + e.Next
	}
	return "Setup stopped: " + err.Error() + "\nNext step: " + againNext
}

// Run installs or repairs Case File Manager. It needs administrator rights.
func Run(o Options) error {
	if o.Out == nil {
		o.Out = os.Stdout
	}
	r := &runner{o: o, l: newLayout(o.InstallDir)}
	url, err := r.install()
	if err != nil {
		fmt.Fprintln(o.Out)
		fmt.Fprintln(o.Out, plain(err))
		if o.Pause {
			pause(os.Stdin, o.Out)
		}
		return err
	}
	switch {
	case o.Handoff != "":
		if err := os.WriteFile(o.Handoff, []byte(url), 0o600); err != nil {
			r.say("Open %s in your browser.", url)
		}
	case !o.NoBrowser:
		OpenBrowser(url)
	}
	if o.Pause {
		r.say("\nThis window closes by itself in 15 seconds.")
		time.Sleep(15 * time.Second)
	}
	return nil
}

func pause(in io.Reader, out io.Writer) {
	fmt.Fprint(out, "\nPress Enter to close this window.")
	_, _ = bufio.NewReader(in).ReadString('\n')
}

func (r *runner) install() (string, error) {
	o, l := r.o, r.l
	if !Elevated() {
		return "", fail("setup needs administrator rights.", againNext+" Choose Yes when Windows asks for permission.")
	}
	self, err := os.Executable()
	if err != nil {
		return "", err
	}
	src := filepath.Dir(self)
	if _, err := os.Stat(filepath.Join(src, "app", "requirements-windows.txt")); err != nil && !sameFile(src, l.root) {
		return "", fail("the app folder isn't next to Setup.exe.", "Extract the whole install zip (right-click it, Extract All), then double-click Setup.exe in the extracted folder.")
	}

	r.step(1, "Checking this computer")
	vol := filepath.VolumeName(o.InstallDir) + `\`
	var free, total, totalFree uint64
	if err := windows.GetDiskFreeSpaceEx(windows.StringToUTF16Ptr(vol), &free, &total, &totalFree); err == nil {
		gb := float64(free) / (1 << 30)
		if gb < 20 {
			return "", fail(fmt.Sprintf("only %.1f GB is free on drive %s.", gb, vol), "Free up space so at least 20 GB is available, then double-click Setup.exe again.")
		}
		r.say("%.1f GB free on drive %s.", gb, vol)
	}
	if out, err := exec.Command("manage-bde", "-status", filepath.VolumeName(o.InstallDir)).CombinedOutput(); err == nil && strings.Contains(string(out), "Protection Off") {
		r.say("Warning: BitLocker is off for drive %s. Case files would be stored unencrypted on this disk.", vol)
		r.say("         Turning on BitLocker is strongly recommended for confidential material.")
	}

	r.step(2, "Stopping Case File Manager if it's running")
	if found, err := winsvc.StopAll(); err != nil {
		return "", fail("Windows' service manager didn't answer: "+err.Error()+".", againNext)
	} else if found {
		time.Sleep(2 * time.Second)
		r.say("Stopped the existing services. Your cases are kept.")
	} else {
		r.say("Nothing installed yet.")
	}

	r.step(3, "Copying the launcher and the app to "+l.root)
	for _, d := range l.dirs() {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return "", fail("the folder "+d+" couldn't be created.", "Check free disk space, then double-click Setup.exe again.")
		}
	}
	if !sameFile(self, l.exe()) {
		if err := copyFile(self, l.exe()); err != nil {
			return "", fail("the launcher couldn't be copied: "+err.Error()+".", "Restart the computer, then double-click Setup.exe again.")
		}
	}
	if !sameFile(src, l.root) {
		appSrc := filepath.Join(src, "app")
		if err := os.RemoveAll(l.app); err != nil {
			return "", fail("the old app folder couldn't be replaced: "+err.Error()+".", "Restart the computer, then double-click Setup.exe again.")
		}
		if err := copyDir(appSrc, l.app); err != nil {
			return "", fail("the app couldn't be copied: "+err.Error()+".", "Check free disk space, then double-click Setup.exe again.")
		}
	}
	_ = os.WriteFile(filepath.Join(l.root, "uninstall.cmd"), []byte(uninstallCmd), 0o644)
	_ = os.Remove(filepath.Join(l.root, "uninstall.ps1")) // left by older installs
	if pub := os.Getenv("PUBLIC"); pub != "" {
		_ = os.WriteFile(filepath.Join(pub, "Desktop", shortcutName), []byte(shortcut(o.Port)), 0o644)
	}

	r.step(4, "Registering the Windows services")
	existing, _ := os.ReadFile(l.configPath())
	cfg, err := mergeConfig(existing, l, o)
	if err != nil {
		return "", fail(err.Error()+".", "Delete "+l.configPath()+", then double-click Setup.exe again.")
	}
	if err := os.WriteFile(l.configPath(), cfg, 0o644); err != nil {
		return "", fail("launcher.json couldn't be written.", againNext)
	}
	if err := winsvc.Install(l.exe(), l.configPath()); err != nil {
		return "", fail("the Windows services couldn't be registered: "+err.Error()+".", againNext)
	}
	r.say("Registered the launcher and the Web app, Background worker and Local AI models services.")

	r.step(5, "Creating the HTTPS certificates and the setup code")
	if out, err := exec.Command(l.exe(), "--config", l.configPath(), "prepare").CombinedOutput(); err != nil {
		return "", fail("the certificates couldn't be created: "+strings.TrimSpace(string(out))+".", "Check free disk space, then double-click Setup.exe again.")
	}

	r.step(6, "Setting folder permissions")
	for _, rule := range aclPlan(l) {
		for _, args := range icaclsArgs(rule) {
			if out, err := exec.Command("icacls", args...).CombinedOutput(); err != nil {
				return "", fail("permissions on "+rule.Path+" couldn't be set: "+strings.TrimSpace(string(out))+".", againNext)
			}
		}
	}
	r.say("Each service can reach only its own files. The launcher's credentials and the certificate authority are Administrators-only.")

	r.step(7, "Setting firewall rules")
	for _, tail := range firewallDeletes() {
		_, _ = netsh(tail) // fails harmlessly when the rule isn't there
	}
	for _, tail := range firewallRules(l, o.AppPort) {
		if out, err := netsh(tail); err != nil {
			return "", fail("a firewall rule couldn't be added: "+strings.TrimSpace(out)+".", againNext)
		}
	}
	r.say("Port %d is open on Private networks only. Public and Domain networks stay closed.", o.AppPort)
	r.say("The background worker can't reach the internet. The Control Center (port %d) only listens on this computer.", o.Port)

	r.step(8, "Starting the launcher")
	if err := winsvc.Start(LauncherService); err != nil && !strings.Contains(err.Error(), "already running") {
		return "", fail("the launcher didn't start: "+err.Error()+".", "Open "+filepath.Join(l.state, "launcher.log")+" for the reason, then double-click Setup.exe again.")
	}
	if !waitPort(o.Port, 60*time.Second) {
		return "", fail("the launcher didn't start.", "Open "+filepath.Join(l.state, "launcher.log")+" for the reason, then double-click Setup.exe again.")
	}

	code := ""
	if b, err := os.ReadFile(filepath.Join(l.state, "setup-code.txt")); err == nil {
		code = setupCode(string(b))
	}
	url := controlCenterURL(o.Port, code)
	r.say("")
	r.say("Case File Manager is installed.")
	if code != "" {
		r.say("Your one-time setup code is %s (also in %s, which only Administrators can open).", code, filepath.Join(l.state, "setup-code.txt"))
	} else {
		r.say("Setup is already complete. Sign in with your Admin account.")
	}
	r.say("The Control Center is opening in your browser. It installs Python, Tesseract OCR, Ollama and")
	r.say("the embedding model by itself (about 3 GB), then starts every service. You can watch it there.")
	r.say("Next time, open it from the \"Case File Manager Control Center\" shortcut on the desktop.")
	r.say("Your browser warns about the certificate the first time; trust-certificate.md explains how to trust it.")
	return url, nil
}

// Uninstall removes the services, firewall rules and desktop shortcut. It
// keeps case data, the audit log and the certificates.
func Uninstall(o Options) error {
	if !Elevated() {
		code, err := RunElevated([]string{"uninstall", "--pause"})
		if err != nil {
			fmt.Println(plain(err))
			pause(os.Stdin, os.Stdout)
		}
		if code != 0 {
			return errors.New("uninstall didn't finish")
		}
		return nil
	}
	err := winsvc.Uninstall()
	for _, tail := range firewallDeletes() {
		_, _ = netsh(tail)
	}
	if pub := os.Getenv("PUBLIC"); pub != "" {
		_ = os.Remove(filepath.Join(pub, "Desktop", shortcutName))
	}
	if err != nil {
		fmt.Println("Some services couldn't be removed: " + err.Error())
		fmt.Println("Next step: restart the computer, then double-click uninstall.cmd again.")
	} else {
		fmt.Println("Removed the services, firewall rules and desktop shortcut.")
		fmt.Printf("Your case data, audit log and certificates are still in %s. Delete that folder yourself if you no longer need them.\n", o.InstallDir)
	}
	if o.Pause {
		pause(os.Stdin, os.Stdout)
	}
	return err
}

// netsh runs "netsh advfirewall firewall <tail>". netsh needs name="..."
// quoted inside the argument, so the command line is passed as is.
func netsh(tail string) (string, error) {
	sys := filepath.Join(os.Getenv("SystemRoot"), "System32", "netsh.exe")
	cmd := exec.Command(sys)
	cmd.SysProcAttr = &syscall.SysProcAttr{CmdLine: `"` + sys + `" advfirewall firewall ` + tail}
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func waitPort(port int, max time.Duration) bool {
	deadline := time.Now().Add(max)
	for time.Now().Before(deadline) {
		c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), time.Second)
		if err == nil {
			c.Close()
			return true
		}
		time.Sleep(time.Second)
	}
	return false
}

func sameFile(a, b string) bool {
	sa, err1 := os.Stat(a)
	sb, err2 := os.Stat(b)
	return err1 == nil && err2 == nil && os.SameFile(sa, sb)
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(tmp)
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, dst)
}

func copyDir(src, dst string) error {
	return filepath.WalkDir(src, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, p)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		if !d.Type().IsRegular() {
			return nil
		}
		return copyFile(p, target)
	})
}

// OpenBrowser opens url in the default browser.
func OpenBrowser(url string) {
	_ = windows.ShellExecute(0, windows.StringToUTF16Ptr("open"), windows.StringToUTF16Ptr(url), nil, nil, windows.SW_SHOWNORMAL)
}

// shellExecuteInfo is SHELLEXECUTEINFOW.
type shellExecuteInfo struct {
	cbSize       uint32
	fMask        uint32
	hwnd         windows.Handle
	lpVerb       *uint16
	lpFile       *uint16
	lpParameters *uint16
	lpDirectory  *uint16
	nShow        int32
	hInstApp     windows.Handle
	lpIDList     uintptr
	lpClass      *uint16
	hkeyClass    windows.Handle
	dwHotKey     uint32
	hIcon        windows.Handle
	hProcess     windows.Handle
}

var procShellExecuteEx = windows.NewLazySystemDLL("shell32.dll").NewProc("ShellExecuteExW")

// RunElevated runs this program again with args after Windows asks for
// administrator rights, waits for it, and returns its exit code.
func RunElevated(args []string) (uint32, error) {
	exe, err := os.Executable()
	if err != nil {
		return 1, err
	}
	var quoted []string
	for _, a := range args {
		quoted = append(quoted, syscall.EscapeArg(a))
	}
	info := shellExecuteInfo{
		fMask:        0x40, // SEE_MASK_NOCLOSEPROCESS
		lpVerb:       windows.StringToUTF16Ptr("runas"),
		lpFile:       windows.StringToUTF16Ptr(exe),
		lpParameters: windows.StringToUTF16Ptr(strings.Join(quoted, " ")),
		lpDirectory:  windows.StringToUTF16Ptr(filepath.Dir(exe)),
		nShow:        windows.SW_SHOWNORMAL,
	}
	info.cbSize = uint32(unsafe.Sizeof(info))
	if ok, _, callErr := procShellExecuteEx.Call(uintptr(unsafe.Pointer(&info))); ok == 0 {
		if errors.Is(callErr, windows.ERROR_CANCELLED) {
			return 1, fail("Windows didn't give setup permission to make changes.", againNext+" Choose Yes when Windows asks. If you can't, ask someone with an administrator account.")
		}
		return 1, callErr
	}
	defer windows.CloseHandle(info.hProcess)
	if _, err := windows.WaitForSingleObject(info.hProcess, windows.INFINITE); err != nil {
		return 1, err
	}
	var code uint32
	if err := windows.GetExitCodeProcess(info.hProcess, &code); err != nil {
		return 1, err
	}
	return code, nil
}
