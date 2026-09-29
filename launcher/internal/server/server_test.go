package server

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"testing/fstest"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/auth"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/docker"
)

type fakeEngine struct {
	mu       sync.Mutex
	down     bool
	restarts int
}

func (f *fakeEngine) Version(context.Context) (docker.Version, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.down {
		return docker.Version{}, docker.ErrUnavailable
	}
	return docker.Version{Version: "29.0.0"}, nil
}

func (f *fakeEngine) List(context.Context) ([]docker.Container, error) {
	return []docker.Container{
		{ID: "c-api", Labels: map[string]string{"com.docker.compose.project": "casefiles", "com.docker.compose.service": "api"}},
		// Not in the catalog: must be ignored.
		{ID: "c-x", Labels: map[string]string{"com.docker.compose.project": "casefiles", "com.docker.compose.service": "rogue"}},
	}, nil
}

func (f *fakeEngine) Inspect(_ context.Context, id string) (docker.Inspect, error) {
	var i docker.Inspect
	i.ID = id
	i.State.Status, i.State.Running = "running", true
	i.State.StartedAt = time.Now().Add(-time.Minute)
	i.Config.Image = "casefiles-api:0.1.0"
	f.mu.Lock()
	i.RestartCount = f.restarts
	f.mu.Unlock()
	return i, nil
}

func (f *fakeEngine) Stats(context.Context, string) (docker.Usage, error) {
	return docker.Usage{CPUPercent: 2.5, MemoryBytes: 100 << 20}, nil
}

func (f *fakeEngine) Logs(context.Context, string, int) ([]docker.LogLine, error) {
	return []docker.LogLine{{Time: "t", Stream: "stdout", Text: "loaded key sk-abcdefghijklmnopqrstu"}}, nil
}

type fakeRunner struct {
	mu    sync.Mutex
	calls [][]string
}

func (f *fakeRunner) Run(_ context.Context, args []string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, args)
	return "", nil
}

func (f *fakeRunner) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

type harness struct {
	t      *testing.T
	srv    *Server
	h      http.Handler
	runner *fakeRunner
	code   string
	cfg    config.Config
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	dir := t.TempDir()
	cfg, _ := config.Load(filepath.Join(dir, "launcher.json"))
	cfg.AppPort = 0 // any free port for the port check
	_ = os.MkdirAll(cfg.LauncherDir(), 0o700)
	store, code, err := auth.Open(cfg.LauncherDir())
	if err != nil {
		t.Fatal(err)
	}
	al, err := audit.Open(filepath.Join(cfg.LauncherDir(), "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	runner := &fakeRunner{}
	srv := New(Options{
		Config: cfg, Auth: store, Audit: al, Engine: &fakeEngine{}, Runner: runner,
		Web: fstest.MapFS{"index.html": {Data: []byte("<!doctype html>cc")}},
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err := srv.EnsureCertificates(); err != nil {
		t.Fatal(err)
	}
	srv.poller.refresh(context.Background())
	return &harness{t: t, srv: srv, h: srv.Handler(), runner: runner, code: code, cfg: cfg}
}

type client struct {
	h      *harness
	cookie *http.Cookie
	csrf   string
	ip     string
}

func (h *harness) client() *client { return &client{h: h, ip: "127.0.0.1"} }

func (c *client) do(method, path, body string, mutate ...func(*http.Request)) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, "https://127.0.0.1:8443"+path, strings.NewReader(body))
	r.RemoteAddr = c.ip + ":50000"
	r.Host = "127.0.0.1:8443"
	if method != http.MethodGet {
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Origin", "https://127.0.0.1:8443")
		if c.csrf != "" {
			r.Header.Set(csrfHeader, c.csrf)
		}
	}
	if c.cookie != nil {
		r.AddCookie(c.cookie)
	}
	for _, m := range mutate {
		m(r)
	}
	w := httptest.NewRecorder()
	c.h.h.ServeHTTP(w, r)
	for _, ck := range w.Result().Cookies() {
		if ck.Name == sessionCookie {
			if ck.MaxAge < 0 {
				c.cookie = nil
			} else {
				c.cookie = ck
			}
		}
	}
	if strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") {
		var sv sessionView
		if json.Unmarshal(w.Body.Bytes(), &sv) == nil && sv.CSRF != "" {
			c.csrf = sv.CSRF
		}
	}
	return w
}

func (h *harness) admin() *client {
	c := h.client()
	if w := c.do("POST", "/api/session/setup-code", `{"code":"`+h.code+`"}`); w.Code != 200 {
		h.t.Fatalf("setup code: %d %s", w.Code, w.Body)
	}
	if w := c.do("POST", "/api/session/admin", `{"username":"fred","password":"correct horse battery"}`); w.Code != 200 {
		h.t.Fatalf("create admin: %d %s", w.Code, w.Body)
	}
	return c
}

func (h *harness) waitRuns(n int) {
	h.t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for h.runner.count() < n && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if got := h.runner.count(); got != n {
		h.t.Fatalf("expected %d compose runs, got %d", n, got)
	}
}

func TestNetworkGuard(t *testing.T) {
	h := newHarness(t)
	c := h.client()
	c.ip = "192.168.1.50"
	if w := c.do("GET", "/api/session", ""); w.Code != http.StatusForbidden {
		t.Fatalf("LAN client with LAN off: %d", w.Code)
	}
	_ = h.cfg.SaveState(config.State{LANEnabled: true})
	if w := c.do("GET", "/api/session", ""); w.Code != http.StatusOK {
		t.Fatalf("LAN client with LAN on: %d", w.Code)
	}
	c.ip = "203.0.113.9"
	if w := c.do("GET", "/api/session", ""); w.Code != http.StatusForbidden {
		t.Fatalf("public client must always be refused: %d", w.Code)
	}
}

func TestUnauthenticatedRequestsRefused(t *testing.T) {
	h := newHarness(t)
	c := h.client()
	for _, p := range []string{"/api/status", "/api/events", "/api/logs?service=api", "/api/audit", "/api/lan-qr"} {
		if w := c.do("GET", p, ""); w.Code != http.StatusUnauthorized {
			t.Errorf("GET %s: %d", p, w.Code)
		}
	}
	if w := c.do("POST", "/api/actions", `{"action":"stack.start_all"}`); w.Code != http.StatusUnauthorized {
		t.Errorf("POST action: %d", w.Code)
	}
	if h.runner.count() != 0 {
		t.Fatal("nothing may run without a session")
	}
}

func TestSetupScopeIsLimited(t *testing.T) {
	h := newHarness(t)
	c := h.client()
	if w := c.do("POST", "/api/session/setup-code", `{"code":"`+h.code+`"}`); w.Code != 200 {
		t.Fatalf("setup: %d", w.Code)
	}
	if w := c.do("POST", "/api/actions", `{"action":"stack.start_all"}`); w.Code != http.StatusAccepted {
		t.Fatalf("start all during setup: %d %s", w.Code, w.Body)
	}
	h.waitRuns(1)
	for _, body := range []string{`{"action":"stack.stop_all"}`, `{"action":"service.stop","service":"api"}`, `{"action":"launcher.set_lan_binding","enabled":true}`, `{"action":"diagnostics.bundle"}`} {
		if w := c.do("POST", "/api/actions", body); w.Code != http.StatusForbidden {
			t.Errorf("%s during setup: %d", body, w.Code)
		}
	}
	if w := c.do("GET", "/api/logs?service=api", ""); w.Code != http.StatusForbidden {
		t.Errorf("logs during setup: %d", w.Code)
	}
}

func TestCSRFAndOrigin(t *testing.T) {
	h := newHarness(t)
	c := h.admin()
	noToken := func(r *http.Request) { r.Header.Del(csrfHeader) }
	if w := c.do("POST", "/api/actions", `{"action":"stack.stop_all"}`, noToken); w.Code != http.StatusForbidden {
		t.Errorf("missing CSRF token: %d", w.Code)
	}
	evil := func(r *http.Request) { r.Header.Set("Origin", "https://evil.example") }
	if w := c.do("POST", "/api/actions", `{"action":"stack.stop_all"}`, evil); w.Code != http.StatusForbidden {
		t.Errorf("cross-origin: %d", w.Code)
	}
	form := func(r *http.Request) { r.Header.Set("Content-Type", "text/plain") }
	if w := c.do("POST", "/api/actions", `{"action":"stack.stop_all"}`, form); w.Code != http.StatusForbidden {
		t.Errorf("non-JSON body: %d", w.Code)
	}
	crossSite := func(r *http.Request) { r.Header.Set("Sec-Fetch-Site", "cross-site") }
	if w := c.do("POST", "/api/session/login", `{"username":"fred","password":"x"}`, crossSite); w.Code != http.StatusForbidden {
		t.Errorf("cross-site login: %d", w.Code)
	}
	if h.runner.count() != 0 {
		t.Fatal("no action should have run")
	}
}

func TestAdminActionsRunAndAreAudited(t *testing.T) {
	h := newHarness(t)
	c := h.admin()
	w := c.do("POST", "/api/actions", `{"action":"service.restart","service":"worker"}`)
	if w.Code != http.StatusAccepted {
		t.Fatalf("restart: %d %s", w.Code, w.Body)
	}
	h.waitRuns(1)
	args := h.runner.calls[0]
	if args[len(args)-1] != "worker" || args[len(args)-2] != "--force-recreate" {
		t.Fatalf("unexpected args %q", args)
	}

	if w := c.do("POST", "/api/actions", `{"action":"service.exec","service":"api","cmd":"sh"}`); w.Code != http.StatusBadRequest {
		t.Fatalf("arbitrary action: %d", w.Code)
	}
	time.Sleep(50 * time.Millisecond)
	if h.runner.count() != 1 {
		t.Fatal("rejected action must not run")
	}

	entries, _ := h.srv.audit.Recent(50)
	seen := map[string]bool{}
	for _, e := range entries {
		seen[e.Action+"/"+e.Outcome] = true
	}
	for _, want := range []string{"service.restart/requested", "service.restart/succeeded", "action.rejected/denied", "auth.create_admin/succeeded"} {
		if !seen[want] {
			t.Errorf("audit missing %s (have %v)", want, seen)
		}
	}
	if n, err := audit.Verify(h.srv.auditPath()); err != nil || n == 0 {
		t.Fatalf("audit chain: %d %v", n, err)
	}
}

func TestActionRefusedWhenAuditUnwritable(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores permission bits")
	}
	h := newHarness(t)
	c := h.admin()
	_ = os.Chmod(h.srv.auditPath(), 0o400)
	defer os.Chmod(h.srv.auditPath(), 0o600)
	if w := c.do("POST", "/api/actions", `{"action":"stack.start_all"}`); w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected refusal, got %d", w.Code)
	}
	time.Sleep(50 * time.Millisecond)
	if h.runner.count() != 0 {
		t.Fatal("action ran without an audit record")
	}
}

func TestStatusAndLogs(t *testing.T) {
	h := newHarness(t)
	c := h.admin()
	w := c.do("GET", "/api/status", "")
	if w.Code != 200 {
		t.Fatalf("status: %d", w.Code)
	}
	var snap Snapshot
	if err := json.Unmarshal(w.Body.Bytes(), &snap); err != nil {
		t.Fatal(err)
	}
	if len(snap.Services) != 6 || !snap.Docker.OK {
		t.Fatalf("snapshot: %+v", snap)
	}
	if snap.Services[0].State != "running" || snap.Services[1].State != "stopped" {
		t.Fatalf("states: %s %s", snap.Services[0].State, snap.Services[1].State)
	}
	if len(snap.Wizard) != 7 || snap.Wizard[2].State != StepDone {
		t.Fatalf("wizard: %+v", snap.Wizard)
	}

	if w := c.do("GET", "/api/logs?service=../../etc", ""); w.Code != http.StatusBadRequest {
		t.Fatalf("unknown service logs: %d", w.Code)
	}
	w = c.do("GET", "/api/logs?service=api&tail=10", "")
	if w.Code != 200 || strings.Contains(w.Body.String(), "sk-abcdefghijklmnopqrstu") {
		t.Fatalf("logs must be redacted: %d %s", w.Code, w.Body)
	}
}

func TestLoginErrorsArePlain(t *testing.T) {
	h := newHarness(t)
	h.admin()
	c := h.client()
	w := c.do("POST", "/api/session/login", `{"username":"fred","password":"nope"}`)
	if w.Code != http.StatusUnauthorized || !strings.Contains(w.Body.String(), "don't match") {
		t.Fatalf("login error: %d %s", w.Code, w.Body)
	}
	w = c.do("POST", "/api/session/login", `{"username":"fred","password":"correct horse battery"}`)
	if w.Code != 200 || c.cookie == nil || !c.cookie.HttpOnly || !c.cookie.Secure || c.cookie.SameSite != http.SameSiteStrictMode {
		t.Fatalf("login cookie: %d %+v", w.Code, c.cookie)
	}
}

func TestAutoRestartReportedAndDockerDown(t *testing.T) {
	h := newHarness(t)
	eng := h.srv.engine.(*fakeEngine)
	eng.mu.Lock()
	eng.restarts = 1
	eng.mu.Unlock()
	h.srv.poller.refresh(context.Background())
	snap := h.srv.poller.get()
	if snap.Services[0].AutoRestart == nil {
		t.Fatal("a rising restart count must be reported as an automatic restart")
	}

	eng.mu.Lock()
	eng.down = true
	eng.mu.Unlock()
	h.srv.poller.refresh(context.Background())
	snap = h.srv.poller.get()
	if snap.Docker.OK || snap.Docker.Problem == nil || snap.Wizard[0].State != StepFailed {
		t.Fatalf("docker down not reported: %+v %+v", snap.Docker, snap.Wizard[0])
	}
}
