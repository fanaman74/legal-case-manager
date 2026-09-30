package components

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

// Progress receives plain-language progress messages.
type Progress func(msg string)

// Error is an install failure with a plain next step for the Admin.
type Error struct {
	What string
	Next string
	Err  error
}

func (e *Error) Error() string { return e.What + " " + e.Next }
func (e *Error) Unwrap() error { return e.Err }

func fail(err error, what, next string) error { return &Error{What: what, Next: next, Err: err} }

// Install installs or updates one component. The caller stops the services
// that use it first (catalog.Component.Stops) and, for the embedding model,
// makes sure the models service is running.
func (m *Manager) Install(ctx context.Context, id catalog.ComponentID, progress Progress) error {
	if !m.managed(id) {
		return fail(nil, "The Control Center can only install this on Windows.", "Install it yourself, then refresh this page.")
	}
	switch id {
	case catalog.Python:
		return m.installPython(ctx, progress)
	case catalog.Tesseract:
		return m.installTesseract(ctx, progress)
	case catalog.Ollama:
		return m.installOllama(ctx, progress)
	case catalog.Model:
		return m.installModel(ctx, progress)
	}
	return fail(nil, "That component isn't known.", "Reload the page.")
}

func (m *Manager) markInstalled(id catalog.ComponentID) error {
	want, err := m.stamp(id)
	if err != nil {
		return err
	}
	return os.WriteFile(m.markerPath(id), []byte(want+"\n"), 0o644)
}

const netNext = "Check the internet connection, then try again."

func (m *Manager) installPython(ctx context.Context, progress Progress) error {
	if _, err := os.Stat(m.requirements()); err != nil {
		return fail(err, "The app's package list is missing.", "Double-click Setup.exe from the install zip again to restore the app folder.")
	}
	pkg, err := m.download(ctx, catalog.Python, "Python", progress)
	if err != nil {
		return err
	}
	progress("Unpacking Python " + m.pins[catalog.Python].Version + ".")
	// The NuGet package is a plain copy of Python under tools/: no installer,
	// registry entries or PATH changes.
	if err := extract(pkg, "tools/", filepath.Dir(m.cfg.Python)); err != nil {
		return fail(err, "Python couldn't be unpacked.", "Check free disk space, then try again.")
	}
	progress("Setting up Python's package installer.")
	if out, err := m.run(ctx, m.cfg.AppDir, m.cfg.Python, "-m", "ensurepip", "--upgrade", "--default-pip"); err != nil {
		m.log.Error("ensurepip failed", "err", err, "output", out)
		return fail(err, "Python's package installer couldn't be set up.", "Try again. If it fails again, download diagnostics.")
	}
	progress("Installing the app's Python packages. Each one is checked against its hash.")
	out, err := m.run(ctx, m.cfg.AppDir, m.cfg.Python, "-m", "pip", "install",
		"--disable-pip-version-check", "--no-input", "--quiet", "--no-warn-script-location",
		"--require-hashes", "--only-binary=:all:", "-r", m.requirements())
	if err != nil {
		m.log.Error("pip install failed", "err", err, "output", out)
		return fail(err, "The app's Python packages couldn't be installed.", netNext)
	}
	return m.markInstalled(catalog.Python)
}

func (m *Manager) installTesseract(ctx context.Context, progress Progress) error {
	setup, err := m.download(ctx, catalog.Tesseract, "Tesseract OCR", progress)
	if err != nil {
		return err
	}
	progress("Running the Tesseract OCR installer.")
	// The installer ignores /D and always installs to Program Files.
	if out, err := m.run(ctx, filepath.Dir(setup), setup, "/S"); err != nil {
		m.log.Error("tesseract installer failed", "err", err, "output", out)
		return fail(err, "The Tesseract OCR installer didn't finish.", "Try again. If antivirus software asked about it, allow it.")
	}
	// It can hand the work to a copy of itself and return early.
	if !waitForFile(ctx, m.cfg.Tesseract, m.fileWait) {
		return fail(nil, "Tesseract OCR didn't appear at "+m.cfg.Tesseract+".", "Try again. If antivirus software asked about it, allow it.")
	}
	return m.markInstalled(catalog.Tesseract)
}

func (m *Manager) installOllama(ctx context.Context, progress Progress) error {
	zipPath, err := m.download(ctx, catalog.Ollama, "Ollama", progress)
	if err != nil {
		return err
	}
	progress("Unpacking Ollama " + m.pins[catalog.Ollama].Version + ".")
	// The standalone zip, run as a service by the launcher (not the tray app).
	if err := extract(zipPath, "", filepath.Dir(m.cfg.Ollama)); err != nil {
		return fail(err, "Ollama couldn't be unpacked.", "Check free disk space, then try again.")
	}
	return m.markInstalled(catalog.Ollama)
}

func (m *Manager) installModel(ctx context.Context, progress Progress) error {
	name := m.cfg.EmbeddingModel
	progress("Downloading " + name + ".")
	err := m.models.pull(ctx, name, func(done, total int64) {
		progress(fmt.Sprintf("Downloading %s: %s.", name, percent(done, total)))
	})
	if err != nil {
		return fail(err, name+" couldn't be downloaded.", netNext+" The Local AI models service must be running.")
	}
	if !m.modelInstalled() {
		return fail(nil, name+" finished downloading but isn't in the models folder.", "Try again. If it fails again, download diagnostics.")
	}
	return nil
}

func waitForFile(ctx context.Context, p string, max time.Duration) bool {
	deadline := time.Now().Add(max)
	for {
		if _, err := os.Stat(p); err == nil {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(2 * time.Second):
		}
	}
}

// download fetches a pinned file into the download folder and checks its
// SHA-256. A file that is already there and matches is reused.
func (m *Manager) download(ctx context.Context, id catalog.ComponentID, label string, progress Progress) (string, error) {
	pin := m.pins[id]
	dir := m.cfg.DownloadDir()
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fail(err, "The download folder couldn't be created.", "Check free disk space, then try again.")
	}
	dest := filepath.Join(dir, pin.File)
	if sum, err := fileSHA256(dest); err == nil && sum == pin.SHA256 {
		return dest, nil
	}
	progress(fmt.Sprintf("Downloading %s %s.", label, pin.Version))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, pin.URL, nil)
	if err != nil {
		return "", fail(err, label+" couldn't be downloaded.", netNext)
	}
	resp, err := m.client.Do(req)
	if err != nil {
		return "", fail(err, label+" couldn't be downloaded.", netNext)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fail(fmt.Errorf("HTTP %d", resp.StatusCode), label+" couldn't be downloaded (the server answered "+resp.Status+").", netNext)
	}
	tmp := dest + ".part"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return "", fail(err, "The download couldn't be saved.", "Check free disk space, then try again.")
	}
	h := sha256.New()
	pr := &progressReader{r: resp.Body, total: resp.ContentLength, every: time.Second, report: func(done, total int64) {
		progress(fmt.Sprintf("Downloading %s %s: %s.", label, pin.Version, percent(done, total)))
	}}
	_, err = io.Copy(io.MultiWriter(f, h), pr)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return "", fail(err, label+" couldn't be downloaded.", netNext)
	}
	if got := hex.EncodeToString(h.Sum(nil)); got != pin.SHA256 {
		os.Remove(tmp)
		m.log.Error("download checksum mismatch", "component", id, "want", pin.SHA256, "got", got)
		return "", fail(errors.New("checksum mismatch"), "The "+label+" download doesn't match its checksum, so it wasn't used.",
			"Try again. If this keeps happening, something on the network is changing downloads; tell your IT contact.")
	}
	if err := os.Rename(tmp, dest); err != nil {
		return "", fail(err, "The download couldn't be saved.", "Check free disk space, then try again.")
	}
	return dest, nil
}

type progressReader struct {
	r      io.Reader
	done   int64
	total  int64
	every  time.Duration
	last   time.Time
	report func(done, total int64)
}

func (p *progressReader) Read(b []byte) (int, error) {
	n, err := p.r.Read(b)
	p.done += int64(n)
	if now := time.Now(); now.Sub(p.last) >= p.every {
		p.last = now
		p.report(p.done, p.total)
	}
	return n, err
}

func percent(done, total int64) string {
	if total <= 0 {
		return fmt.Sprintf("%.0f MB so far", float64(done)/1e6)
	}
	return fmt.Sprintf("%d%% of %.1f GB", done*100/total, float64(total)/1e9)
}

// extract unpacks the entries of a zip under prefix into dest, replacing
// dest. It refuses entries that would land outside dest.
func extract(zipPath, prefix, dest string) error {
	zr, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer zr.Close()
	staging := dest + ".new"
	if err := os.RemoveAll(staging); err != nil {
		return err
	}
	if err := os.MkdirAll(staging, 0o755); err != nil {
		return err
	}
	for _, f := range zr.File {
		name := strings.ReplaceAll(f.Name, `\`, "/")
		if !strings.HasPrefix(name, prefix) {
			continue
		}
		rel := strings.TrimPrefix(name, prefix)
		if rel == "" {
			continue
		}
		clean := path.Clean("/" + rel)[1:]
		if clean == "" || clean != strings.TrimSuffix(rel, "/") || strings.Contains(rel, ":") {
			return fmt.Errorf("unsafe path in archive: %q", f.Name)
		}
		target := filepath.Join(staging, filepath.FromSlash(clean))
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if !f.Mode().IsRegular() {
			return fmt.Errorf("unexpected file type in archive: %q", f.Name)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := writeEntry(f, target); err != nil {
			return err
		}
	}
	if err := os.RemoveAll(dest); err != nil {
		return err
	}
	return os.Rename(staging, dest)
}

func writeEntry(f *zip.File, target string) error {
	rc, err := f.Open()
	if err != nil {
		return err
	}
	defer rc.Close()
	out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, rc); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

// runProgram runs a fixed program with no console window and returns the
// tail of its output for the log.
func runProgram(ctx context.Context, dir, p string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, p, args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1", "PIP_NO_CACHE_DIR=1")
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	hideWindow(cmd)
	err := cmd.Run()
	s := out.String()
	if len(s) > 4000 {
		s = s[len(s)-4000:]
	}
	return s, err
}
