// Package procs runs the Case File Manager's app processes: it builds each
// service's fixed command line from the compiled-in catalog and the
// installer's config, runs it with its output going to a redacted, rotated
// log file, and makes sure it dies when whoever started it dies.
//
// Nothing here takes input from the browser. A service is chosen by catalog
// ID; the program, arguments and environment are fixed.
package procs

import (
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/tlsca"
)

// Spec is everything needed to start one service.
type Spec struct {
	Service catalog.ServiceID
	Path    string
	Args    []string
	Dir     string
	Env     []string
	LogFile string
}

// ServiceLogDir is the one folder a service may write its log to. Each
// service gets its own folder so the installer can give each service account
// write access to its own log only.
func ServiceLogDir(cfg config.Config, id catalog.ServiceID) string {
	return filepath.Join(cfg.LogDir, string(id))
}

// LogFile is where the service's output is written.
func LogFile(cfg config.Config, id catalog.ServiceID) string {
	return filepath.Join(ServiceLogDir(cfg, id), "service.log")
}

// StatusFile is where a Windows service host records its child's PID and
// exit history for the launcher to read.
func StatusFile(cfg config.Config, id catalog.ServiceID) string {
	return filepath.Join(ServiceLogDir(cfg, id), "status.json")
}

// HeartbeatFile is written by the worker every few seconds.
func HeartbeatFile(cfg config.Config) string {
	return filepath.Join(cfg.DataDir, ".run", "worker-heartbeat")
}

// ModelsDir is where the local AI models are stored.
func ModelsDir(cfg config.Config) string { return filepath.Join(cfg.DataDir, "models") }

// passEnv are the variables a child inherits from its parent. Everything else
// is dropped so nothing unexpected (proxy credentials, tokens) leaks into the
// app processes.
var passEnv = []string{
	"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "PATH",
	"TEMP", "TMP", "TMPDIR", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
	"PROGRAMFILES", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
	"HOME", "LANG", "LC_ALL", "TZ",
}

func baseEnv() []string {
	var out []string
	for _, kv := range os.Environ() {
		k, _, ok := strings.Cut(kv, "=")
		if !ok {
			continue
		}
		for _, p := range passEnv {
			if strings.EqualFold(k, p) {
				out = append(out, kv)
				break
			}
		}
	}
	return out
}

// Specs returns the fixed spec for every catalog service.
func Specs(cfg config.Config, version string) map[catalog.ServiceID]Spec {
	python := []string{"PYTHONUNBUFFERED=1", "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1", "PYTHONUTF8=1"}
	app := append([]string{"DEPLOY_MODE=local", "DATA_DIR=" + cfg.DataDir, "APP_VERSION=" + version}, python...)
	env := func(extra ...string) []string { return append(baseEnv(), extra...) }

	return map[catalog.ServiceID]Spec{
		catalog.API: {
			Service: catalog.API,
			Path:    cfg.Python,
			Args:    []string{"-m", "app.serve"},
			Dir:     cfg.AppDir,
			Env: env(append(app,
				"HOST=0.0.0.0",
				"PORT="+strconv.Itoa(cfg.AppPort),
				"TLS_CERT="+filepath.Join(cfg.CertsDir, tlsca.ServerCert),
				"TLS_KEY="+filepath.Join(cfg.CertsDir, tlsca.ServerKey),
			)...),
			LogFile: LogFile(cfg, catalog.API),
		},
		catalog.Worker: {
			Service: catalog.Worker,
			Path:    cfg.Python,
			Args:    []string{"-m", "app.worker"},
			Dir:     cfg.AppDir,
			Env:     env(append(app, "TESSERACT_CMD="+cfg.Tesseract, "OLLAMA_URL="+ModelsURL(cfg))...),
			LogFile: LogFile(cfg, catalog.Worker),
		},
		catalog.Models: {
			Service: catalog.Models,
			Path:    cfg.Ollama,
			Args:    []string{"serve"},
			Dir:     filepath.Dir(cfg.Ollama),
			Env: env(
				"OLLAMA_HOST=127.0.0.1:"+strconv.Itoa(cfg.ModelsPort),
				"OLLAMA_MODELS="+ModelsDir(cfg),
				"OLLAMA_NOPRUNE=1",
				// Empty keeps Ollama's default: only pages on this computer
				// may call it from a browser.
				"OLLAMA_ORIGINS=",
				// Never send prompts or case text to Ollama's cloud models.
				"OLLAMA_NO_CLOUD=1",
			),
			LogFile: LogFile(cfg, catalog.Models),
		},
	}
}

// ModelsURL is the loopback address of the local AI models.
func ModelsURL(cfg config.Config) string {
	return "http://127.0.0.1:" + strconv.Itoa(cfg.ModelsPort)
}

// Exe adds .exe on Windows.
func Exe(name string) string {
	if runtime.GOOS == "windows" {
		return name + ".exe"
	}
	return name
}
