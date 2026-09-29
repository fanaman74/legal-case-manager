// Package config loads the launcher's settings. The installer writes the file;
// the only setting the browser can change is the LAN switch, which is kept in
// a separate state file.
package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
)

// Config is launcher.json.
type Config struct {
	// DataDir holds case data. It is mounted into the app containers, so
	// nothing belonging to the launcher may live inside it.
	DataDir string `json:"data_dir"`
	// StateDir holds the launcher's credentials, audit log and settings.
	// It is never mounted into a container.
	StateDir string `json:"state_dir"`
	// CertsDir holds the local CA and server certificate. Only the server
	// certificate and key are mounted into the web app, read-only.
	CertsDir string `json:"certs_dir"`
	// ComposeFile is the absolute path to deploy/compose.yaml.
	ComposeFile string `json:"compose_file"`
	// Project is the compose project name.
	Project string `json:"project"`
	// Port is the Control Center's HTTPS port.
	Port int `json:"port"`
	// AppPort is the web app's HTTPS port on the LAN.
	AppPort int `json:"app_port"`
	// Docker is the docker CLI path; empty means look it up on PATH.
	Docker string `json:"docker"`
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
	if c.ComposeFile == "" {
		c.ComposeFile = filepath.Join(base, "deploy", "compose.yaml")
	}
	if c.Project == "" {
		c.Project = "casefiles"
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
	c.ComposeFile, _ = filepath.Abs(c.ComposeFile)
	return c, nil
}

// LauncherDir holds auth, audit and state files.
func (c Config) LauncherDir() string { return c.StateDir }

// CertDir holds the local CA and server certificate.
func (c Config) CertDir() string { return c.CertsDir }

// State is what the Admin can change from the browser.
type State struct {
	LANEnabled bool `json:"lan_enabled"`
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
