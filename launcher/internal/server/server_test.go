package server

import (
	"context"
	"encoding/json"
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
	"testing/fstest"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/auth"
	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/components"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/health"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
	"github.com/fanaman74/legal-case-manager/launcher/internal/supervisor"
)

type fakeSupervisor struct {
	mu       sync.Mutex
	readyErr error
	calls    []string
	infos    map[catalog.ServiceID]supervisor.Info
}

func newFakeSupervisor() *fakeSupervisor {
	return &fakeSupervisor{infos: map[catalog.ServiceID]supervisor.Info{}}
}

func (f *fakeSupervisor) Name() string { return "Test services" }

func (f *fakeSupervisor) Ready(context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.readyErr
}

func (f *fakeSupervisor) Start(_ context.Context, id catalog.ServiceID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, "start:"+string(id))
	started := time.Now().Add(-time.Hour) // past the start grace period
	f.infos[id] = supervisor.Info{Phase: supervisor.Running, Status: procs.Status{Service: id, Running: true, PID: os.Getpid(), StartedAt: &started}}
	return nil
}

func (f *fakeSupervisor) Stop(_ context.Context, id catalog.ServiceID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, "stop:"+string(id))
	f.infos[id] = supervisor.Info{Phase: supervisor.Stopped, Status: procs.Status{Service: id, LastExit: &procs.Exit{}}}
	return nil
}

func (f *fakeSupervisor) Info(_ context.Context, id catalog.ServiceID) (supervisor.Info, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if in, ok := f.infos[id]; ok {
		return in, nil
	}
	return supervisor.Info{Phase: supervisor.Stopped, Status: procs.Status{Service: id}}, nil
}

func (f *fakeSupervisor) set(id catalog.ServiceID, fn func(*supervisor.Info)) {
	f.mu.Lock()
	defer f.mu.Unlock()
	in := f.infos[id]
	fn(&in)
	f.infos[id] = in
}

func (f *fakeSupervisor) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

func (f *fakeSupervisor) callList() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.calls...)
}

type fakeProber struct {
	mu        sync.Mutex
	unhealthy map[catalog.ServiceID]bool
}

func (f *fakeProber) Probe(_ context.Context, id catalog.ServiceID) health.Result {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.unhealthy[id] {
		return health.Result{Detail: "it isn't answering yet", At: time.Now()}
	}
	return health.Result{OK: true, Version: "0.2.0", At: time.Now()}
}

// fakeInstaller pretends to install components. Everything starts installed
// unless a test marks it missing.
type fakeInstaller struct {
	mu        sync.Mutex
	missing   map[catalog.ComponentID]bool
	failWith  map[catalog.ComponentID]error
	installed []catalog.ComponentID
	// block, when set, holds each install until a value arrives.
	block chan struct{}
	// during records the supervisor calls made before each install ran.
	sup    *fakeSupervisor
	during map[catalog.ComponentID][]string
}

func (f *fakeInstaller) Check() []components.Status {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []components.Status
	for _, c := range catalog.Components {
		st := components.Status{ID: c.ID, Name: c.Name, State: components.Installed, CanInstall: true, Detail: "Installed."}
		if f.missing[c.ID] {
			st.State, st.Detail = components.Missing, "Not installed yet."
		}
		out = append(out, st)
	}
	return out
}

func (f *fakeInstaller) Needed() []catalog.ComponentID {
	var out []catalog.ComponentID
	for _, st := range f.Check() {
		if st.State != components.Installed {
			out = append(out, st.ID)
		}
	}
	return out
}

func (f *fakeInstaller) Install(ctx context.Context, id catalog.ComponentID, progress components.Progress) error {
	progress("Downloading " + string(id) + ": 50% of 1.0 GB.")
	if f.block != nil {
		select {
		case <-f.block:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.during == nil {
		f.during = map[catalog.ComponentID][]string{}
	}
	f.during[id] = f.sup.callList()
	if err := f.failWith[id]; err != nil {
		return err
	}
	f.installed = append(f.installed, id)
	delete(f.missing, id)
	return nil
}

func (f *fakeInstaller) setMissing(ids ...catalog.ComponentID) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.missing = map[catalog.ComponentID]bool{}
	for _, id := range ids {
		f.missing[id] = true
	}
}

type harness struct {
	t      *testing.T
	srv    *Server
	h      http.Handler
	sup    *fakeSupervisor
	prober *fakeProber
	comps  *fakeInstaller
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
	sup, prober := newFakeSupervisor(), &fakeProber{unhealthy: map[catalog.ServiceID]bool{}}
	comps := &fakeInstaller{sup: sup, missing: map[catalog.ComponentID]bool{}, failWith: map[catalog.ComponentID]error{}}
	srv := New(Options{
		Config: cfg, Auth: store, Audit: al, Supervisor: sup, Prober: prober, Components: comps,
		Web: fstest.MapFS{"index.html": {Data: []byte("<!doctype html>cc")}},
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err := srv.EnsureCertificates(); err != nil {
		t.Fatal(err)
	}
	srv.poller.refresh(context.Background())
	return &harness{t: t, srv: srv, h: srv.Handler(), sup: sup, prober: prober, comps: comps, code: code, cfg: cfg}
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
	for h.sup.count() < n && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if got := h.sup.count(); got != n {
		h.t.Fatalf("expected %d supervisor calls, got %d: %v", n, got, h.sup.callList())
	}
}

// waitOp waits for the latest operation to finish and returns it.
func (h *harness) waitOp() Operation {
	h.t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if ops := h.srv.ops.list(); len(ops) > 0 && ops[0].State != OpRunning {
			return ops[0]
		}
		time.Sleep(10 * time.Millisecond)
	}
	h.t.Fatal("operation didn't finish")
	return Operation{}
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
	if h.sup.count() != 0 {
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
	h.waitRuns(3)
	h.waitOp()
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
	if h.sup.count() != 0 {
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
	if op := h.waitOp(); op.State != OpSucceeded {
		t.Fatalf("restart failed: %+v", op)
	}
	if got := strings.Join(h.sup.callList(), " "); got != "stop:worker start:worker" {
		t.Fatalf("restart calls: %s", got)
	}

	if w := c.do("POST", "/api/actions", `{"action":"service.exec","service":"api","cmd":"sh"}`); w.Code != http.StatusBadRequest {
		t.Fatalf("arbitrary action: %d", w.Code)
	}
	time.Sleep(50 * time.Millisecond)
	if h.sup.count() != 2 {
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

func TestStartAllOrderAndWantedState(t *testing.T) {
	h := newHarness(t)
	c := h.admin()
	if w := c.do("POST", "/api/actions", `{"action":"stack.start_all"}`); w.Code != http.StatusAccepted {
		t.Fatalf("start all: %d", w.Code)
	}
	if op := h.waitOp(); op.State != OpSucceeded || op.Message != "All services started." {
		t.Fatalf("start all: %+v", op)
	}
	if got := strings.Join(h.sup.callList(), " "); got != "start:models start:api start:worker" {
		t.Fatalf("start order: %s", got)
	}
	if got := strings.Join(h.cfg.LoadState().Wanted, ","); got != "api,worker,models" {
		t.Fatalf("wanted after start all: %s", got)
	}
	c.do("POST", "/api/actions", `{"action":"service.stop","service":"worker"}`)
	h.waitOp()
	if got := strings.Join(h.cfg.LoadState().Wanted, ","); got != "api,models" {
		t.Fatalf("wanted after stopping worker: %s", got)
	}
	c.do("POST", "/api/actions", `{"action":"stack.stop_all"}`)
	h.waitOp()
	if got := h.sup.callList(); strings.Join(got[len(got)-3:], " ") != "stop:worker stop:api stop:models" {
		t.Fatalf("stop order: %v", got)
	}
	if len(h.cfg.LoadState().Wanted) != 0 {
		t.Fatalf("wanted after stop all: %v", h.cfg.LoadState().Wanted)
	}
}

func TestWantedServicesStartAfterRestart(t *testing.T) {
	h := newHarness(t)
	_ = h.cfg.SaveState(config.State{Wanted: []string{"worker", "api"}})
	h.srv.startWanted(context.Background())
	if got := strings.Join(h.sup.callList(), " "); got != "start:api start:worker" {
		t.Fatalf("auto start: %s", got)
	}
	entries, _ := h.srv.audit.Recent(10)
	var requested, succeeded bool
	for _, e := range entries {
		if e.Actor == "launcher" && e.Action == "stack.start_all" {
			requested = requested || e.Outcome == audit.Requested
			succeeded = succeeded || e.Outcome == audit.Succeeded
		}
	}
	if !requested || !succeeded {
		t.Fatalf("automatic start must be audited: %+v", entries)
	}
}

func TestStartFailsWhenServiceUnhealthy(t *testing.T) {
	h := newHarness(t)
	c := h.admin()
	h.prober.mu.Lock()
	h.prober.unhealthy[catalog.Worker] = true
	h.prober.mu.Unlock()
	c.do("POST", "/api/actions", `{"action":"service.start","service":"worker"}`)
	op := h.waitOp()
	if op.State != OpFailed || !strings.Contains(op.Message, "not responding") {
		t.Fatalf("unhealthy start should fail with a next step: %+v", op)
	}
	entries, _ := h.srv.audit.Recent(5)
	if last := entries[0]; last.Action != "service.start" || last.Outcome != audit.Failed {
		t.Fatalf("failure must be audited: %+v", last)
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
	if h.sup.count() != 0 {
		t.Fatal("action ran without an audit record")
	}
}

func TestStatusAndLogs(t *testing.T) {
	h := newHarness(t)
	c := h.admin()
	_ = h.sup.Start(context.Background(), catalog.API)
	h.srv.poller.refresh(context.Background())
	w := c.do("GET", "/api/status", "")
	if w.Code != 200 {
		t.Fatalf("status: %d", w.Code)
	}
	var snap Snapshot
	if err := json.Unmarshal(w.Body.Bytes(), &snap); err != nil {
		t.Fatal(err)
	}
	if len(snap.Services) != len(catalog.Services) || !snap.Runtime.OK {
		t.Fatalf("snapshot: %+v", snap)
	}
	if snap.Services[0].State != "running" || snap.Services[0].Version != "0.2.0" || snap.Services[1].State != "stopped" {
		t.Fatalf("states: %+v", snap.Services)
	}
	if snap.Services[0].Usage == nil || snap.Services[0].Usage.MemoryBytes == 0 {
		t.Errorf("running service should report memory: %+v", snap.Services[0].Usage)
	}
	if len(snap.Wizard) != 7 || snap.Wizard[1].State != StepDone || snap.Wizard[3].State != StepDone {
		t.Fatalf("wizard: %+v", snap.Wizard)
	}

	if w := c.do("GET", "/api/logs?service=../../etc", ""); w.Code != http.StatusBadRequest {
		t.Fatalf("unknown service logs: %d", w.Code)
	}
	_ = os.MkdirAll(procs.ServiceLogDir(h.cfg, catalog.API), 0o755)
	lw, err := procs.OpenLog(procs.LogFile(h.cfg, catalog.API))
	if err != nil {
		t.Fatal(err)
	}
	lw.Line("stdout", "loaded key sk-abcdefghijklmnopqrstu")
	lw.Line("stderr", "Traceback: boom")
	lw.Close()
	w = c.do("GET", "/api/logs?service=api&tail=10", "")
	if w.Code != 200 || strings.Contains(w.Body.String(), "sk-abcdefghijklmnopqrstu") || !strings.Contains(w.Body.String(), `"level":"error"`) {
		t.Fatalf("logs must be redacted and levelled: %d %s", w.Code, w.Body)
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

func TestAutoRestartAndBrokenInstall(t *testing.T) {
	h := newHarness(t)
	_ = h.sup.Start(context.Background(), catalog.API)
	restarted := time.Now()
	h.sup.set(catalog.API, func(in *supervisor.Info) { in.Restarts, in.LastRestartAt = 1, &restarted })
	h.srv.poller.refresh(context.Background())
	snap := h.srv.poller.get()
	if snap.Services[0].AutoRestart == nil || snap.Services[0].RestartCount != 1 {
		t.Fatal("an automatic restart must be reported")
	}

	h.sup.mu.Lock()
	h.sup.readyErr = errors.New("The app services aren't registered with Windows. Double-click Setup.exe from the install zip again. It repairs the installation and keeps your cases.")
	h.sup.mu.Unlock()
	h.srv.poller.refresh(context.Background())
	snap = h.srv.poller.get()
	if snap.Runtime.OK || snap.Runtime.Problem == nil || snap.Wizard[0].State != StepFailed {
		t.Fatalf("broken install not reported: %+v %+v", snap.Runtime, snap.Wizard[0])
	}
}

func auditActions(t *testing.T, h *harness) []string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(h.cfg.LauncherDir(), "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		var e audit.Entry
		if json.Unmarshal([]byte(line), &e) == nil {
			out = append(out, e.Actor+" "+e.Action+" "+e.Target+" "+e.Outcome)
		}
	}
	return out
}

func contains(list []string, want string) bool {
	for _, x := range list {
		if x == want {
			return true
		}
	}
	return false
}

func TestInstallComponentStopsAndRestartsItsServices(t *testing.T) {
	h := newHarness(t)
	_ = h.cfg.SaveState(config.State{Initialized: true})
	c := h.admin()
	for _, id := range []catalog.ServiceID{catalog.API, catalog.Worker, catalog.Models} {
		_ = h.sup.Start(context.Background(), id)
	}
	h.comps.setMissing(catalog.Python)

	w := c.do("POST", "/api/actions", `{"action":"component.install","component":"python"}`)
	if w.Code != http.StatusAccepted {
		t.Fatalf("install: %d %s", w.Code, w.Body)
	}
	op := h.waitOp()
	if op.State != OpSucceeded {
		t.Fatalf("op: %+v", op)
	}
	// The web app and worker use Python, so they were stopped first and
	// started again afterwards. The models service was left alone.
	during := strings.Join(h.comps.during[catalog.Python], ",")
	if !strings.HasSuffix(during, "stop:api,stop:worker") {
		t.Fatalf("calls before the install ran: %s", during)
	}
	calls := strings.Join(h.sup.callList(), ",")
	if !strings.HasSuffix(calls, "stop:api,stop:worker,start:api,start:worker") || strings.Contains(calls, "stop:models") {
		t.Fatalf("supervisor calls: %s", calls)
	}
	if !contains(auditActions(t, h), "fred component.install python succeeded") {
		t.Fatalf("audit: %v", auditActions(t, h))
	}
}

func TestInstallFailureIsShownAndServicesComeBack(t *testing.T) {
	h := newHarness(t)
	_ = h.cfg.SaveState(config.State{Initialized: true})
	c := h.admin()
	_ = h.sup.Start(context.Background(), catalog.Models)
	h.comps.setMissing(catalog.Ollama)
	h.comps.failWith[catalog.Ollama] = &components.Error{What: "Ollama couldn't be downloaded.", Next: "Check the internet connection, then try again."}

	if w := c.do("POST", "/api/actions", `{"action":"component.install","component":"ollama"}`); w.Code != http.StatusAccepted {
		t.Fatalf("install: %d %s", w.Code, w.Body)
	}
	op := h.waitOp()
	if op.State != OpFailed || op.Message != "Ollama couldn't be downloaded. Check the internet connection, then try again." {
		t.Fatalf("op: %+v", op)
	}
	h.srv.poller.refresh(context.Background())
	var ollama components.Status
	for _, st := range h.srv.poller.get().Components {
		if st.ID == catalog.Ollama {
			ollama = st
		}
	}
	if ollama.State != components.Failed || !strings.Contains(ollama.Detail, "internet connection") {
		t.Fatalf("status after failure: %+v", ollama)
	}
	if calls := strings.Join(h.sup.callList(), ","); !strings.HasSuffix(calls, "stop:models,start:models") {
		t.Fatalf("models service not restarted: %s", calls)
	}
	if !contains(auditActions(t, h), "fred component.install ollama failed") {
		t.Fatalf("audit: %v", auditActions(t, h))
	}
}

func TestFirstStartInstallsEverythingAndStartsAllServices(t *testing.T) {
	h := newHarness(t)
	h.comps.setMissing(catalog.Python, catalog.Tesseract, catalog.Ollama, catalog.Model)

	h.srv.autoSetup(context.Background())

	op := h.waitOp()
	if op.State != OpSucceeded || op.Actor != "launcher" {
		t.Fatalf("op: %+v", op)
	}
	got := h.comps.installed
	if len(got) != 4 || got[0] != catalog.Python || got[3] != catalog.Model {
		t.Fatalf("installed in order %v", got)
	}
	// The models service is started before the embedding model downloads.
	if during := h.comps.during[catalog.Model]; !contains(during, "start:models") {
		t.Fatalf("models not running for the model download: %v", during)
	}
	for _, svc := range catalog.Services {
		if in, _ := h.sup.Info(context.Background(), svc.ID); in.Phase != supervisor.Running {
			t.Errorf("%s not running after the first install", svc.ID)
		}
	}
	st := h.cfg.LoadState()
	if !st.Initialized || len(st.Wanted) != len(catalog.Services) {
		t.Fatalf("state after first install: %+v", st)
	}
	actions := auditActions(t, h)
	for _, want := range []string{"launcher components.install_missing  requested", "launcher component.install model succeeded", "launcher stack.start_all  succeeded"} {
		if !contains(actions, want) {
			t.Errorf("audit lacks %q: %v", want, actions)
		}
	}
}

func TestLaterStartsOnlyStartWantedServices(t *testing.T) {
	h := newHarness(t)
	_ = h.cfg.SaveState(config.State{Initialized: true, Wanted: []string{"api"}})
	h.srv.autoSetup(context.Background())
	h.waitOp()
	if calls := strings.Join(h.sup.callList(), ","); calls != "start:api" {
		t.Fatalf("calls: %s", calls)
	}
}

func TestSetupCodeHolderCanInstall(t *testing.T) {
	h := newHarness(t)
	_ = h.cfg.SaveState(config.State{Initialized: true})
	c := h.client()
	if w := c.do("POST", "/api/session/setup-code", `{"code":"`+h.code+`"}`); w.Code != 200 {
		t.Fatalf("setup code: %d", w.Code)
	}
	h.comps.setMissing(catalog.Tesseract)
	if w := c.do("POST", "/api/actions", `{"action":"components.install_missing"}`); w.Code != http.StatusAccepted {
		t.Fatalf("install missing during setup: %d %s", w.Code, w.Body)
	}
	if op := h.waitOp(); op.State != OpSucceeded {
		t.Fatalf("op: %+v", op)
	}
	if len(h.comps.installed) != 1 || h.comps.installed[0] != catalog.Tesseract {
		t.Fatalf("installed %v", h.comps.installed)
	}
}

func TestOtherActionsWaitForAnInstall(t *testing.T) {
	h := newHarness(t)
	_ = h.cfg.SaveState(config.State{Initialized: true})
	c := h.admin()
	h.comps.setMissing(catalog.Ollama)
	h.comps.block = make(chan struct{})
	if w := c.do("POST", "/api/actions", `{"action":"component.install","component":"ollama"}`); w.Code != http.StatusAccepted {
		t.Fatalf("install: %d", w.Code)
	}
	// Wait until the install reports progress, then check the overlay.
	deadline := time.Now().Add(2 * time.Second)
	var ollama components.Status
	for time.Now().Before(deadline) {
		h.srv.poller.refresh(context.Background())
		for _, st := range h.srv.poller.get().Components {
			if st.ID == catalog.Ollama {
				ollama = st
			}
		}
		if strings.Contains(ollama.Detail, "50%") {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if ollama.State != components.Installing || !strings.Contains(ollama.Detail, "50%") {
		t.Fatalf("while installing: %+v", ollama)
	}
	w := c.do("POST", "/api/actions", `{"action":"stack.start_all"}`)
	if w.Code != http.StatusConflict || !strings.Contains(w.Body.String(), "50%") {
		t.Fatalf("start all during install: %d %s", w.Code, w.Body)
	}
	close(h.comps.block)
	if op := h.waitOp(); op.State != OpSucceeded {
		t.Fatalf("op: %+v", op)
	}
}
