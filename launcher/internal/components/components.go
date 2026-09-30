// Package components checks and installs the programs the app needs: Python
// with the app's packages, Tesseract OCR, Ollama and the embedding model.
//
// Nothing here takes input from the browser. A component is chosen by catalog
// ID, and each download's URL and SHA-256 are compiled into the launcher
// (runtimes.json), so the Control Center can only ever install exactly these
// files.
package components

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
)

//go:embed runtimes.json
var pinsJSON []byte

// Pin is one pinned download.
type Pin struct {
	Version string `json:"version"`
	URL     string `json:"url"`
	File    string `json:"file"`
	SHA256  string `json:"sha256"`
}

// Pins returns the compiled-in downloads, keyed by component.
func Pins() (map[catalog.ComponentID]Pin, error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(pinsJSON, &raw); err != nil {
		return nil, err
	}
	out := map[catalog.ComponentID]Pin{}
	for _, id := range []catalog.ComponentID{catalog.Python, catalog.Tesseract, catalog.Ollama} {
		var p Pin
		if err := json.Unmarshal(raw[string(id)], &p); err != nil {
			return nil, fmt.Errorf("runtimes.json: %s: %w", id, err)
		}
		p.SHA256 = strings.ToLower(p.SHA256)
		if len(p.SHA256) != 64 || p.URL == "" || p.File == "" || p.File != filepath.Base(p.File) || strings.ContainsAny(p.File, `/\`) {
			return nil, fmt.Errorf("runtimes.json: %s is incomplete", id)
		}
		out[id] = p
	}
	return out, nil
}

// State is where a component stands.
type State string

const (
	Installed  State = "installed"
	Missing    State = "missing"
	Outdated   State = "outdated"
	Installing State = "installing"
	Failed     State = "failed"
)

// Status is one row of the Components list.
type Status struct {
	ID      catalog.ComponentID `json:"id"`
	Name    string              `json:"name"`
	Purpose string              `json:"purpose"`
	State   State               `json:"state"`
	// Version is the installed version, or the one that will be installed.
	Version string `json:"version,omitempty"`
	Detail  string `json:"detail"`
	// CanInstall is false where the launcher can't install the component
	// itself (development on Linux and macOS).
	CanInstall bool `json:"canInstall"`
}

// Runner runs one program to completion and returns its combined output.
type Runner func(ctx context.Context, dir, path string, args ...string) (string, error)

// Manager checks and installs components.
type Manager struct {
	cfg  config.Config
	pins map[catalog.ComponentID]Pin
	log  *slog.Logger
	// goos is runtime.GOOS; tests override it.
	goos string
	// client downloads the pinned files.
	client *http.Client
	// run runs installers and pip.
	run Runner
	// models downloads the embedding model through the models service.
	models *ollamaClient
	// fileWait is how long to wait for an installer's files to appear.
	fileWait time.Duration
}

// New returns a Manager for cfg.
func New(cfg config.Config, log *slog.Logger) (*Manager, error) {
	pins, err := Pins()
	if err != nil {
		return nil, err
	}
	return &Manager{
		cfg:      cfg,
		pins:     pins,
		log:      log,
		goos:     runtime.GOOS,
		client:   &http.Client{Transport: &http.Transport{Proxy: http.ProxyFromEnvironment}},
		run:      runProgram,
		models:   newOllamaClient(procs.ModelsURL(cfg)),
		fileWait: 3 * time.Minute,
	}, nil
}

// managed reports whether the launcher installs this component itself.
func (m *Manager) managed(id catalog.ComponentID) bool {
	return id == catalog.Model || m.goos == "windows"
}

func (m *Manager) exe(id catalog.ComponentID) string {
	switch id {
	case catalog.Python:
		return m.cfg.Python
	case catalog.Tesseract:
		return m.cfg.Tesseract
	case catalog.Ollama:
		return m.cfg.Ollama
	}
	return ""
}

func (m *Manager) markerPath(id catalog.ComponentID) string {
	return filepath.Join(m.cfg.RuntimeDir, string(id)+".installed")
}

// stamp identifies exactly what an install of id puts on disk. For Python it
// covers the app's package list too, so an app upgrade with new packages
// counts as an update.
func (m *Manager) stamp(id catalog.ComponentID) (string, error) {
	pin := m.pins[id]
	if id != catalog.Python {
		return pin.SHA256, nil
	}
	sum, err := fileSHA256(m.requirements())
	if err != nil {
		return "", err
	}
	return pin.SHA256 + ":" + sum, nil
}

func (m *Manager) requirements() string {
	return filepath.Join(m.cfg.AppDir, "requirements-windows.txt")
}

// modelInstalled reports whether the embedding model's manifest exists.
func (m *Manager) modelInstalled() bool {
	name, tag, ok := strings.Cut(m.cfg.EmbeddingModel, ":")
	if !ok {
		tag = "latest"
	}
	_, err := os.Stat(filepath.Join(procs.ModelsDir(m.cfg), "manifests", "registry.ollama.ai", "library", name, tag))
	return err == nil
}

// Check returns every component's status. It only reads a few small files,
// so it is cheap enough to run on every status refresh.
func (m *Manager) Check() []Status {
	out := make([]Status, 0, len(catalog.Components))
	for _, c := range catalog.Components {
		out = append(out, m.check(c))
	}
	return out
}

func (m *Manager) check(c catalog.Component) Status {
	st := Status{ID: c.ID, Name: c.Name, Purpose: c.Purpose, CanInstall: m.managed(c.ID)}
	if c.ID == catalog.Model {
		st.Version = m.cfg.EmbeddingModel
		if m.modelInstalled() {
			st.State, st.Detail = Installed, m.cfg.EmbeddingModel+" is downloaded."
		} else {
			st.State, st.Detail = Missing, m.cfg.EmbeddingModel+" isn't downloaded yet (about 1.2 GB). It needs the Local AI models service running."
		}
		return st
	}
	pin := m.pins[c.ID]
	_, exeErr := os.Stat(m.exe(c.ID))
	if !st.CanInstall {
		if exeErr == nil {
			st.State, st.Detail = Installed, "Installed outside the Control Center."
		} else {
			st.State, st.Detail = Missing, "Not found at "+m.exe(c.ID)+". Install it yourself; the Control Center installs it on Windows only."
		}
		return st
	}
	st.Version = pin.Version
	if exeErr != nil {
		st.State, st.Detail = Missing, "Not installed yet."
		return st
	}
	want, err := m.stamp(c.ID)
	if err != nil {
		st.State, st.Detail = Failed, "The app's package list is missing. Run install.ps1 again to restore the app folder."
		st.CanInstall = false
		return st
	}
	have, _ := os.ReadFile(m.markerPath(c.ID))
	if strings.TrimSpace(string(have)) != want {
		st.State, st.Detail = Outdated, "Version "+pin.Version+" is ready to install."
		if c.ID == catalog.Python {
			st.Detail = "Python " + pin.Version + " or the app's packages need installing."
		}
		return st
	}
	st.State, st.Detail = Installed, "Version "+pin.Version+" is installed."
	return st
}

// Needed lists the components that are missing or out of date and that the
// launcher can install, in install order.
func (m *Manager) Needed() []catalog.ComponentID {
	var out []catalog.ComponentID
	for _, st := range m.Check() {
		if st.CanInstall && (st.State == Missing || st.State == Outdated) {
			out = append(out, st.ID)
		}
	}
	return out
}

func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
