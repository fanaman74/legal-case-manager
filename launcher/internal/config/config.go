// Package config loads the launcher's settings. The installer writes the file;
// the only setting the browser can change is the LAN switch, which is kept in
// a separate state file.
package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
)

// Config is launcher.json. The installer writes it; it holds no secrets.
type Config struct {
	// DataDir holds case data. The app services can write to it, so nothing
	// belonging to the launcher may live inside it.
	DataDir string `json:"data_dir"`
	// StateDir holds the launcher's credentials, audit log and settings.
	// Only Administrators and SYSTEM can read it; the app services can't.
	StateDir string `json:"state_dir"`
	// CertsDir holds the local CA and server certificate. The web app can
	// read the server certificate and key only, never the CA key.
	CertsDir string `json:"certs_dir"`
	// AppDir holds the web app and worker source (services/api).
	AppDir string `json:"app_dir"`
	// RuntimeDir holds Python (with the app's packages) and Ollama, which the
	// launcher installs.
	RuntimeDir string `json:"runtime_dir"`
	// LogDir holds one log file per service.
	LogDir string `json:"log_dir"`
	// Python, Tesseract and Ollama override the executables inside
	// RuntimeDir (used for development on Linux and macOS).
	Python    string `json:"python"`
	Tesseract string `json:"tesseract"`
	Ollama    string `json:"ollama"`
	// Supervisor chooses how services are run: "windows" (a Windows service
	// per app process, the default on Windows) or "direct" (child processes
	// of the launcher, the default elsewhere).
	Supervisor string `json:"supervisor"`
	// Port is the Control Center's HTTPS port.
	Port int `json:"port"`
	// AppPort is the web app's HTTPS port on the LAN.
	AppPort int `json:"app_port"`
	// ModelsPort is the loopback port the local AI models listen on.
	ModelsPort int `json:"models_port"`
	// EmbeddingModel is the model the wizard checks for.
	EmbeddingModel string `json:"embedding_model"`
	// Hostname is an extra DNS name for the certificate, e.g. casefiles.local.
	Hostname string `json:"hostname"`
}

// DefaultPath is where the installer puts launcher.json.
func DefaultPath() string {
	if runtime.GOOS == "windows" {
		return `C:\CaseFiles\launcher.json`
	}
	return "launcher.json"
}

// Load reads path and fills defaults.
func Load(path string) (Config, error) {
	c := Config{}
	b, err := os.ReadFile(path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return c, err
	}
	if err == nil {
		// Windows PowerShell 5.1 writes UTF-8 with a byte-order mark.
		b = bytes.TrimPrefix(b, []byte("\xef\xbb\xbf"))
		if err := json.Unmarshal(b, &c); err != nil {
			return c, err
		}
	}
	base := filepath.Dir(path)
	if c.DataDir == "" {
		c.DataDir = filepath.Join(base, "data")
	}
	if c.StateDir == "" {
		c.StateDir = filepath.Join(base, "launcher")
	}
	if c.CertsDir == "" {
		c.CertsDir = filepath.Join(base, "certs")
	}
	if c.AppDir == "" {
		c.AppDir = filepath.Join(base, "app")
	}
	if c.RuntimeDir == "" {
		c.RuntimeDir = filepath.Join(base, "runtime")
	}
	if c.LogDir == "" {
		c.LogDir = filepath.Join(base, "logs")
	}
	exe := ""
	if runtime.GOOS == "windows" {
		exe = ".exe"
	}
	if c.Python == "" {
		if runtime.GOOS == "windows" {
			c.Python = filepath.Join(c.RuntimeDir, "python", "python.exe")
		} else {
			c.Python = filepath.Join(c.RuntimeDir, "venv", "bin", "python")
		}
	}
	if c.Tesseract == "" {
		c.Tesseract = filepath.Join(c.RuntimeDir, "tesseract", "tesseract"+exe)
		// Tesseract's Windows installer ignores the folder it is given and
		// always installs here.
		if pf := os.Getenv("ProgramFiles"); runtime.GOOS == "windows" && pf != "" {
			c.Tesseract = filepath.Join(pf, "Tesseract-OCR", "tesseract.exe")
		}
	}
	if c.Ollama == "" {
		c.Ollama = filepath.Join(c.RuntimeDir, "ollama", "ollama"+exe)
	}
	if c.Supervisor == "" {
		c.Supervisor = "direct"
		if runtime.GOOS == "windows" {
			c.Supervisor = "windows"
		}
	}
	if c.ModelsPort == 0 {
		c.ModelsPort = 11434
	}
	if c.Port == 0 {
		c.Port = 8443
	}
	if c.AppPort == 0 {
		c.AppPort = 443
	}
	if c.EmbeddingModel == "" {
		c.EmbeddingModel = "bge-m3"
	}
	if c.Hostname == "" {
		c.Hostname = "casefiles.local"
	}
	c.DataDir, _ = filepath.Abs(c.DataDir)
	c.StateDir, _ = filepath.Abs(c.StateDir)
	c.CertsDir, _ = filepath.Abs(c.CertsDir)
	for _, p := range []*string{&c.AppDir, &c.RuntimeDir, &c.LogDir, &c.Python, &c.Tesseract, &c.Ollama} {
		*p, _ = filepath.Abs(*p)
	}
	return c, nil
}

// LauncherDir holds auth, audit and state files.
func (c Config) LauncherDir() string { return c.StateDir }

// DownloadDir keeps verified downloads. It is inside StateDir so the app
// services can't swap an installer before the launcher runs it.
func (c Config) DownloadDir() string { return filepath.Join(c.StateDir, "downloads") }

// CertDir holds the local CA and server certificate.
func (c Config) CertDir() string { return c.CertsDir }

// State is what the Admin can change from the browser.
type State struct {
	LANEnabled bool `json:"lan_enabled"`
	// Initialized is set once the launcher has installed the components and
	// started every service for the first time.
	Initialized bool `json:"initialized,omitempty"`
	// Wanted lists the services the Admin last started. The launcher starts
	// them again when the computer restarts, even if nobody signs in.
	Wanted []string `json:"wanted,omitempty"`
}

func (c Config) statePath() string { return filepath.Join(c.LauncherDir(), "state.json") }

// LoadState reads state.json (missing means defaults).
func (c Config) LoadState() State {
	var s State
	if b, err := os.ReadFile(c.statePath()); err == nil {
		_ = json.Unmarshal(b, &s)
	}
	return s
}

// SaveState writes state.json atomically.
func (c Config) SaveState(s State) error {
	b, _ := json.MarshalIndent(s, "", "  ")
	tmp := c.statePath() + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, c.statePath())
}
