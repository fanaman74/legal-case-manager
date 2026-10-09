package components

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
)

func TestCompiledPinsAreComplete(t *testing.T) {
	pins, err := Pins()
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []catalog.ComponentID{catalog.Python, catalog.Tesseract, catalog.Ollama} {
		p := pins[id]
		if len(p.SHA256) != 64 || !strings.HasPrefix(p.URL, "https://") {
			t.Errorf("%s: incomplete pin %+v", id, p)
		}
	}
}

func zipBytes(t *testing.T, files map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for name, body := range files {
		w, err := zw.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = io.WriteString(w, body)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func sum(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func TestExtractTakesPrefixAndReplacesTarget(t *testing.T) {
	dir := t.TempDir()
	zp := filepath.Join(dir, "p.zip")
	os.WriteFile(zp, zipBytes(t, map[string]string{
		"tools/python.exe":      "py",
		"tools/Lib/os.py":       "os",
		"python.nuspec":         "skip",
		"tools\\DLLs\\x.pyd":    "dll",
		"[Content_Types].xml":   "skip",
		"tools/":                "",
		"tools/Lib/site/":       "",
		"tools/Lib/site/a.py":   "a",
		"other/tools/inner.txt": "skip",
	}), 0o644)
	dest := filepath.Join(dir, "python")
	os.MkdirAll(dest, 0o755)
	os.WriteFile(filepath.Join(dest, "stale.txt"), []byte("old"), 0o644)

	if err := extract(zp, "tools/", dest); err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{"python.exe", "Lib/os.py", "DLLs/x.pyd", "Lib/site/a.py"} {
		if _, err := os.Stat(filepath.Join(dest, f)); err != nil {
			t.Errorf("%s missing: %v", f, err)
		}
	}
	for _, f := range []string{"stale.txt", "python.nuspec", "inner.txt"} {
		if _, err := os.Stat(filepath.Join(dest, f)); err == nil {
			t.Errorf("%s should not be there", f)
		}
	}
}

func TestExtractRefusesPathsOutsideTarget(t *testing.T) {
	for _, name := range []string{"../evil.exe", "a/../../evil.exe", "/abs/evil.exe", "C:/Windows/evil.exe", "..\\evil.exe", "a/./b"} {
		dir := t.TempDir()
		zp := filepath.Join(dir, "p.zip")
		os.WriteFile(zp, zipBytes(t, map[string]string{"ok.txt": "ok", name: "bad"}), 0o644)
		dest := filepath.Join(dir, "out")
		if err := extract(zp, "", dest); err == nil {
			t.Errorf("%q: expected refusal", name)
		}
		if _, err := os.Stat(filepath.Join(dir, "evil.exe")); err == nil {
			t.Errorf("%q: wrote outside the target", name)
		}
		if _, err := os.Stat(dest); err == nil {
			t.Errorf("%q: target replaced despite the refusal", name)
		}
	}
}

// fixture is a Windows-mode Manager whose downloads come from a local server.
type fixture struct {
	m     *Manager
	cfg   config.Config
	hits  map[string]int
	files map[string][]byte
	mu    sync.Mutex
	ran   [][]string
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	root := t.TempDir()
	cfg := config.Config{
		DataDir: filepath.Join(root, "data"), StateDir: filepath.Join(root, "launcher"),
		AppDir: filepath.Join(root, "app"), RuntimeDir: filepath.Join(root, "runtime"),
		Python:         filepath.Join(root, "runtime", "python", "python.exe"),
		Tesseract:      filepath.Join(root, "pf", "Tesseract-OCR", "tesseract.exe"),
		Ollama:         filepath.Join(root, "runtime", "ollama", "ollama.exe"),
		EmbeddingModel: "bge-m3", ModelsPort: 11434,
	}
	for _, d := range []string{cfg.AppDir, cfg.RuntimeDir, cfg.StateDir} {
		os.MkdirAll(d, 0o755)
	}
	os.WriteFile(filepath.Join(cfg.AppDir, "requirements-windows.txt"), []byte("fastapi==1 --hash=sha256:x\n"), 0o644)

	f := &fixture{cfg: cfg, hits: map[string]int{}, files: map[string][]byte{
		"python":    zipBytes(t, map[string]string{"tools/python.exe": "py", "tools/Lib/os.py": "os"}),
		"tesseract": []byte("MZ-installer"),
		"ollama":    zipBytes(t, map[string]string{"ollama.exe": "ol", "lib/ollama/ggml.dll": "dll"}),
	}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimPrefix(r.URL.Path, "/")
		f.mu.Lock()
		f.hits[id]++
		body, ok := f.files[id]
		f.mu.Unlock()
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Write(body)
	}))
	t.Cleanup(srv.Close)

	pins := map[catalog.ComponentID]Pin{}
	for id, name := range map[catalog.ComponentID]string{catalog.Python: "python.nupkg", catalog.Tesseract: "tess-setup.exe", catalog.Ollama: "ollama.zip"} {
		pins[id] = Pin{Version: "1.0", URL: srv.URL + "/" + string(id), File: name, SHA256: sum(f.files[string(id)])}
	}
	f.m = &Manager{
		cfg: cfg, pins: pins, log: slog.New(slog.NewTextHandler(io.Discard, nil)), goos: "windows",
		client: srv.Client(), models: newOllamaClient("http://127.0.0.1:1"), fileWait: 50 * time.Millisecond,
	}
	f.m.run = func(ctx context.Context, dir, p string, args ...string) (string, error) {
		f.mu.Lock()
		f.ran = append(f.ran, append([]string{p}, args...))
		f.mu.Unlock()
		if strings.HasSuffix(p, "tess-setup.exe") {
			os.MkdirAll(filepath.Dir(cfg.Tesseract), 0o755)
			os.WriteFile(cfg.Tesseract, []byte("tess"), 0o755)
		}
		return "", nil
	}
	return f
}

func stateOf(m *Manager, id catalog.ComponentID) Status {
	for _, st := range m.Check() {
		if st.ID == id {
			return st
		}
	}
	return Status{}
}

func TestFreshInstallNeedsEverything(t *testing.T) {
	f := newFixture(t)
	got := f.m.Needed()
	want := []catalog.ComponentID{catalog.Python, catalog.Tesseract, catalog.Ollama, catalog.Model}
	if len(got) != len(want) {
		t.Fatalf("needed = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("needed = %v, want %v (install order matters)", got, want)
		}
	}
}

func TestInstallEachComponent(t *testing.T) {
	f := newFixture(t)
	noop := func(string) {}
	ctx := context.Background()
	for _, id := range []catalog.ComponentID{catalog.Python, catalog.Tesseract, catalog.Ollama} {
		if err := f.m.Install(ctx, id, noop); err != nil {
			t.Fatalf("install %s: %v", id, err)
		}
		if st := stateOf(f.m, id); st.State != Installed {
			t.Fatalf("%s after install: %+v", id, st)
		}
	}
	if _, err := os.Stat(filepath.Join(f.cfg.RuntimeDir, "python", "Lib", "os.py")); err != nil {
		t.Error("python not unpacked from tools/")
	}
	if _, err := os.Stat(filepath.Join(f.cfg.RuntimeDir, "ollama", "lib", "ollama", "ggml.dll")); err != nil {
		t.Error("ollama not unpacked")
	}
	// pip must install only hash-checked wheels from the app's lock file.
	var pip []string
	for _, r := range f.ran {
		if len(r) > 2 && r[2] == "pip" {
			pip = r
		}
	}
	joined := strings.Join(pip, " ")
	for _, flag := range []string{"--require-hashes", "--only-binary=:all:", "-r " + filepath.Join(f.cfg.AppDir, "requirements-windows.txt")} {
		if !strings.Contains(joined, flag) {
			t.Errorf("pip command %q lacks %s", joined, flag)
		}
	}
	// Installing again reuses the verified downloads.
	if err := f.m.Install(ctx, catalog.Ollama, noop); err != nil {
		t.Fatal(err)
	}
	if f.hits["ollama"] != 1 {
		t.Errorf("ollama downloaded %d times, want 1", f.hits["ollama"])
	}
}

func TestChangedPackagesMakePythonOutdated(t *testing.T) {
	f := newFixture(t)
	if err := f.m.Install(context.Background(), catalog.Python, func(string) {}); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(f.cfg.AppDir, "requirements-windows.txt"), []byte("fastapi==2 --hash=sha256:y\n"), 0o644)
	if st := stateOf(f.m, catalog.Python); st.State != Outdated {
		t.Fatalf("python after package change: %+v", st)
	}
}

func TestNewPinMakesComponentOutdated(t *testing.T) {
	f := newFixture(t)
	if err := f.m.Install(context.Background(), catalog.Ollama, func(string) {}); err != nil {
		t.Fatal(err)
	}
	p := f.m.pins[catalog.Ollama]
	p.SHA256, p.Version = strings.Repeat("0", 64), "2.0"
	f.m.pins[catalog.Ollama] = p
	st := stateOf(f.m, catalog.Ollama)
	if st.State != Outdated || st.Version != "2.0" {
		t.Fatalf("ollama with a newer pin: %+v", st)
	}
}

func TestTamperedDownloadIsRefused(t *testing.T) {
	f := newFixture(t)
	f.files["ollama"] = append(f.files["ollama"], 'x')
	err := f.m.Install(context.Background(), catalog.Ollama, func(string) {})
	var ie *Error
	if !errors.As(err, &ie) || !strings.Contains(ie.What, "checksum") {
		t.Fatalf("expected a checksum error, got %v", err)
	}
	if _, err := os.Stat(filepath.Join(f.cfg.RuntimeDir, "ollama")); err == nil {
		t.Error("a download that failed its checksum was unpacked")
	}
	entries, _ := os.ReadDir(f.cfg.DownloadDir())
	if len(entries) != 0 {
		t.Errorf("bad download kept: %v", entries)
	}
}

func TestTesseractInstallerThatLeavesNoFilesFails(t *testing.T) {
	f := newFixture(t)
	f.m.run = func(context.Context, string, string, ...string) (string, error) { return "", nil }
	if err := f.m.Install(context.Background(), catalog.Tesseract, func(string) {}); err == nil {
		t.Fatal("expected failure when tesseract.exe never appears")
	}
	if st := stateOf(f.m, catalog.Tesseract); st.State != Missing {
		t.Fatalf("tesseract: %+v", st)
	}
}

func TestOffWindowsOnlyTheModelIsInstallable(t *testing.T) {
	f := newFixture(t)
	f.m.goos = "linux"
	if got := f.m.Needed(); len(got) != 1 || got[0] != catalog.Model {
		t.Fatalf("needed on linux = %v", got)
	}
	if err := f.m.Install(context.Background(), catalog.Python, func(string) {}); err == nil {
		t.Fatal("python install should be refused off Windows")
	}
}

func TestModelPull(t *testing.T) {
	f := newFixture(t)
	manifest := filepath.Join(f.cfg.DataDir, "models", "manifests", "registry.ollama.ai", "library", "bge-m3", "latest")
	var gotBody string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		gotBody = string(b)
		if r.URL.Path != "/api/pull" {
			http.NotFound(w, r)
			return
		}
		io.WriteString(w, `{"status":"pulling manifest"}`+"\n")
		io.WriteString(w, `{"status":"downloading","total":100,"completed":50}`+"\n")
		os.MkdirAll(filepath.Dir(manifest), 0o755)
		os.WriteFile(manifest, []byte("{}"), 0o644)
		io.WriteString(w, `{"status":"success"}`+"\n")
	}))
	defer srv.Close()
	f.m.models = newOllamaClient(srv.URL)
	var msgs []string
	if err := f.m.Install(context.Background(), catalog.Model, func(s string) { msgs = append(msgs, s) }); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(gotBody, `"model":"bge-m3"`) {
		t.Errorf("pull body = %s", gotBody)
	}
	if st := stateOf(f.m, catalog.Model); st.State != Installed {
		t.Fatalf("model: %+v", st)
	}
	if len(msgs) == 0 {
		t.Error("no progress reported")
	}
}

func TestModelPullError(t *testing.T) {
	f := newFixture(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"error":"pull model manifest: file does not exist"}`+"\n")
	}))
	defer srv.Close()
	f.m.models = newOllamaClient(srv.URL)
	if err := f.m.Install(context.Background(), catalog.Model, func(string) {}); err == nil {
		t.Fatal("expected an error")
	}
}
