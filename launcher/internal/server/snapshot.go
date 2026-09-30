package server

import (
	"context"
	"fmt"
	"net"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/checks"
	"github.com/fanaman74/legal-case-manager/launcher/internal/components"
	"github.com/fanaman74/legal-case-manager/launcher/internal/health"
	"github.com/fanaman74/legal-case-manager/launcher/internal/netguard"
	"github.com/fanaman74/legal-case-manager/launcher/internal/status"
	"github.com/fanaman74/legal-case-manager/launcher/internal/supervisor"
	"github.com/fanaman74/legal-case-manager/launcher/internal/sysinfo"
)

// RuntimeState says whether services can be run at all.
type RuntimeState struct {
	OK bool `json:"ok"`
	// Name says how services are run ("Windows services").
	Name    string          `json:"name"`
	Problem *status.Problem `json:"problem,omitempty"`
}

// LAN describes how other people reach the app.
type LAN struct {
	ControlCenterOnLAN bool     `json:"controlCenterOnLan"`
	AppURLs            []string `json:"appUrls"`
	ControlCenterURLs  []string `json:"controlCenterUrls"`
}

// Snapshot is everything the Control Center shows. It is rebuilt every few
// seconds and streamed to open pages.
type Snapshot struct {
	GeneratedAt time.Time           `json:"generatedAt"`
	Launcher    string              `json:"launcherVersion"`
	Runtime     RuntimeState        `json:"runtime"`
	Services    []status.Service    `json:"services"`
	Components  []components.Status `json:"components"`
	Checks      []checks.Check      `json:"checks"`
	LAN         LAN                 `json:"lan"`
	Operations  []Operation         `json:"operations"`
	Wizard      []WizardStep        `json:"wizard"`
	HasAdmin    bool                `json:"hasAdmin"`
}

type poller struct {
	s  *Server
	mu sync.RWMutex
	// latest is replaced wholesale; readers never see partial updates.
	latest Snapshot
	// raw is each service's state before the "action in progress" overlay,
	// so actions can wait for a real result.
	raw       map[catalog.ServiceID]status.Service
	sampler   *sysinfo.Sampler
	checksAt  time.Time
	checkList []checks.Check
	tools     toolResults
	subs      map[chan Snapshot]struct{}
}

// toolResults caches the slower checks that run a program.
type toolResults struct {
	running   bool
	at        time.Time
	python    string
	pythonErr error
	ocr       string
	ocrErr    error
	pst       string
	pstErr    error
}

func newPoller(s *Server) *poller {
	return &poller{
		s:       s,
		raw:     map[catalog.ServiceID]status.Service{},
		sampler: sysinfo.NewSampler(),
		subs:    map[chan Snapshot]struct{}{},
	}
}

func (p *poller) run(ctx context.Context) {
	t := time.NewTicker(2 * time.Second)
	defer t.Stop()
	p.refresh(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			p.refresh(ctx)
		}
	}
}

func (p *poller) invalidateChecks() {
	p.mu.Lock()
	p.checksAt = time.Time{}
	p.tools.at = time.Time{}
	p.mu.Unlock()
}

func (p *poller) get() Snapshot {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.latest
}

func (p *poller) rawState(id catalog.ServiceID) status.Service {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.raw[id]
}

func (p *poller) subscribe() (chan Snapshot, func()) {
	ch := make(chan Snapshot, 1)
	p.mu.Lock()
	p.subs[ch] = struct{}{}
	p.mu.Unlock()
	return ch, func() {
		p.mu.Lock()
		delete(p.subs, ch)
		p.mu.Unlock()
	}
}

// refresh rebuilds the snapshot. Safe to call from actions to push an update
// immediately.
func (p *poller) refresh(ctx context.Context) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	s := p.s
	now := s.now()
	snap := Snapshot{GeneratedAt: now, Launcher: Version, HasAdmin: s.auth.HasAdmin()}

	readyErr := s.sup.Ready(ctx)
	snap.Runtime = RuntimeState{OK: readyErr == nil, Name: s.sup.Name()}
	if readyErr != nil {
		pr := status.RuntimeProblem(readyErr.Error())
		snap.Runtime.Problem = &pr
	}

	// Read every service and probe the running ones in parallel.
	type result struct {
		info supervisor.Info
		h    *health.Result
	}
	results := make([]result, len(catalog.Services))
	var wg sync.WaitGroup
	for i, svc := range catalog.Services {
		wg.Add(1)
		go func(i int, id catalog.ServiceID) {
			defer wg.Done()
			info, err := s.sup.Info(ctx, id)
			if err != nil {
				info = supervisor.Info{Phase: supervisor.Stopped}
			}
			results[i].info = info
			if info.Phase == supervisor.Running {
				r := s.prober.Probe(ctx, id)
				results[i].h = &r
			}
		}(i, svc.ID)
	}
	wg.Wait()

	busy := s.ops.busyServices()
	raw := map[catalog.ServiceID]status.Service{}
	live := map[int]bool{}
	for i, svc := range catalog.Services {
		st := status.Derive(svc, results[i].info, results[i].h, now)
		if pid := results[i].info.PID; pid > 0 && results[i].info.Running {
			live[pid] = true
			if u, ok := p.sampler.Usage(pid); ok && st.State == status.Running {
				st.Usage = &u
			}
		}
		if readyErr != nil && st.State == status.Stopped {
			st.Detail = "Can't start until the installation is repaired"
		}
		raw[svc.ID] = st
		if label, ok := busy[svc.ID]; ok && st.State != status.Error {
			st.State, st.Detail = status.Starting, label
		}
		snap.Services = append(snap.Services, st)
	}
	p.sampler.Forget(live)

	snap.Components = s.componentStatus()
	snap.Checks = p.systemChecks(snap, readyErr, now)
	snap.LAN = s.lanInfo()
	snap.Operations = s.ops.list()
	snap.Wizard = wizard(snap)

	p.mu.Lock()
	p.raw = raw
	p.latest = snap
	for ch := range p.subs {
		select {
		case ch <- snap:
		default:
		}
	}
	p.mu.Unlock()
}

// refreshTools re-runs the program-based checks at most once a minute, in the
// background so a slow disk never delays the live status.
func (p *poller) refreshTools(now time.Time) toolResults {
	p.mu.Lock()
	t := p.tools
	stale := !t.running && now.Sub(t.at) > time.Minute
	if stale {
		p.tools.running = true
	}
	p.mu.Unlock()
	if stale {
		go func() {
			cfg := p.s.cfg
			var r toolResults
			r.python, r.pythonErr = checks.Probe(cfg.Python, []string{"--version"}, cfg.AppDir)
			r.ocr, r.ocrErr = checks.Probe(cfg.Tesseract, []string{"--version"}, filepath.Dir(cfg.Tesseract))
			r.pst, r.pstErr = checks.Probe(cfg.Python, []string{"-m", "app.toolcheck", "pst"}, cfg.AppDir)
			r.at = p.s.now()
			p.mu.Lock()
			p.tools = r
			p.mu.Unlock()
		}()
	}
	return t
}

func (p *poller) systemChecks(snap Snapshot, readyErr error, now time.Time) []checks.Check {
	s := p.s
	svc := func(id catalog.ServiceID) status.State {
		for _, x := range snap.Services {
			if x.ID == id {
				return x.State
			}
		}
		return status.Stopped
	}
	// Slow checks (disk, audit chain, port probe) run every 10 seconds.
	p.mu.RLock()
	cached, fresh := p.checkList, now.Sub(p.checksAt) < 10*time.Second
	p.mu.RUnlock()
	byID := map[string]checks.Check{}
	if fresh {
		for _, c := range cached {
			byID[c.ID] = c
		}
	} else {
		for _, c := range []checks.Check{
			checks.Disk(s.cfg.DataDir),
			checks.Port(s.cfg.AppPort, svc(catalog.API) == status.Running || svc(catalog.API) == status.Starting),
			checks.Certificate(s.cfg.CertDir(), netguard.LANAddresses(), now),
			checks.Database(s.cfg.DataDir),
			checks.VectorIndex(s.cfg.DataDir),
			checks.AuditChain(s.auditPath()),
		} {
			byID[c.ID] = c
		}
		list := make([]checks.Check, 0, len(byID))
		for _, c := range byID {
			list = append(list, c)
		}
		p.mu.Lock()
		p.checkList, p.checksAt = list, now
		p.mu.Unlock()
	}
	if t := p.refreshTools(now); !t.at.IsZero() {
		byID["runtime"] = checks.Runtime(t.python, t.pythonErr, readyErr, s.sup.Name())
		byID["ocr"] = checks.OCR(t.ocr, t.ocrErr)
		byID["pst"] = checks.PST(t.pst, t.pstErr)
	} else {
		pending := func(id, label string) checks.Check {
			return checks.Check{ID: id, Label: label, Level: checks.Pending, Detail: "Checking…"}
		}
		byID["runtime"], byID["ocr"], byID["pst"] = pending("runtime", "App runtime"), pending("ocr", "OCR"), pending("pst", "PST parser")
		if readyErr != nil {
			byID["runtime"] = checks.Runtime("", nil, readyErr, s.sup.Name())
		}
	}
	order := []string{"runtime", "disk", "port", "cert", "ocr", "pst", "database", "vectors", "audit"}
	out := make([]checks.Check, 0, len(order))
	for _, id := range order {
		if c, ok := byID[id]; ok {
			out = append(out, c)
		}
	}
	return out
}

func (s *Server) lanInfo() LAN {
	st := s.cfg.LoadState()
	out := LAN{ControlCenterOnLAN: st.LANEnabled, AppURLs: []string{}, ControlCenterURLs: []string{}}
	for _, ip := range netguard.LANAddresses() {
		out.AppURLs = append(out.AppURLs, hostURL(ip, s.cfg.AppPort))
		if st.LANEnabled {
			out.ControlCenterURLs = append(out.ControlCenterURLs, hostURL(ip, s.cfg.Port))
		}
	}
	return out
}

func hostURL(ip net.IP, port int) string {
	if port == 443 {
		return "https://" + ip.String() + "/"
	}
	return "https://" + net.JoinHostPort(ip.String(), strconv.Itoa(port)) + "/"
}

// Wizard step states.
const (
	StepTodo        = "todo"
	StepInProgress  = "in_progress"
	StepDone        = "done"
	StepFailed      = "failed"
	StepUnavailable = "unavailable"
)

// WizardStep is one first-run step.
type WizardStep struct {
	N      int    `json:"n"`
	ID     string `json:"id"`
	Title  string `json:"title"`
	State  string `json:"state"`
	Detail string `json:"detail"`
}

func wizard(snap Snapshot) []WizardStep {
	check := func(id string) checks.Level {
		for _, c := range snap.Checks {
			if c.ID == id {
				return c.Level
			}
		}
		return checks.Pending
	}

	pre := WizardStep{N: 1, ID: "prerequisites", Title: "Check prerequisites"}
	failed := 0
	for _, id := range []string{"disk", "port"} {
		if check(id) == checks.Fail {
			failed++
		}
	}
	if !snap.Runtime.OK {
		failed++
	}
	switch {
	case failed > 0:
		pre.State, pre.Detail = StepFailed, fmt.Sprintf("%d check(s) need attention.", failed)
	case check("disk") == checks.Pending:
		pre.State, pre.Detail = StepInProgress, "Checking this computer."
	default:
		pre.State, pre.Detail = StepDone, "The launcher is installed, there is enough disk space, and the web app's port is free."
	}

	comp := WizardStep{N: 2, ID: "components", Title: "Install Python, OCR and the local AI"}
	installed, installing, compFailed := 0, false, ""
	for _, c := range snap.Components {
		switch c.State {
		case components.Installed:
			installed++
		case components.Installing:
			installing = true
		case components.Failed:
			if compFailed == "" {
				compFailed = c.Detail
			}
		}
	}
	switch {
	case installed == len(snap.Components):
		comp.State, comp.Detail = StepDone, "Everything the app needs is installed."
	case installing:
		comp.State, comp.Detail = StepInProgress, fmt.Sprintf("%d of %d installed.", installed, len(snap.Components))
	case compFailed != "":
		comp.State, comp.Detail = StepFailed, compFailed
	default:
		comp.State, comp.Detail = StepTodo, fmt.Sprintf("%d of %d installed. The downloads are about 3 GB.", installed, len(snap.Components))
	}

	svc := WizardStep{N: 3, ID: "services", Title: "Start the services"}
	running, errs, starting := 0, 0, 0
	for _, x := range snap.Services {
		switch x.State {
		case status.Running:
			running++
		case status.Error:
			errs++
		case status.Starting:
			starting++
		}
	}
	total := len(snap.Services)
	var lastStart *Operation
	for i := range snap.Operations {
		if snap.Operations[i].Action == catalog.StackStartAll {
			lastStart = &snap.Operations[i]
			break
		}
	}
	switch {
	case running == total:
		svc.State, svc.Detail = StepDone, "All services are running."
	case errs > 0:
		svc.State, svc.Detail = StepFailed, fmt.Sprintf("%d of %d running. %d need attention.", running, total, errs)
	case starting > 0 || lastStart != nil && lastStart.State == OpRunning:
		svc.State, svc.Detail = StepInProgress, fmt.Sprintf("%d of %d running.", running, total)
	case lastStart != nil && lastStart.State == OpFailed:
		svc.State, svc.Detail = StepFailed, lastStart.Message
	default:
		svc.State, svc.Detail = StepTodo, fmt.Sprintf("%d of %d running.", running, total)
	}

	admin := WizardStep{N: 4, ID: "admin", Title: "Create the Admin account"}
	if snap.HasAdmin {
		admin.State, admin.Detail = StepDone, "The Admin account exists."
	} else {
		admin.State, admin.Detail = StepTodo, "Choose the username and password you'll use to sign in."
	}

	return []WizardStep{
		pre, comp, svc, admin,
		{N: 5, ID: "chat", Title: "Connect a chat provider (optional)", State: StepUnavailable, Detail: "Provider settings arrive with the AI features."},
		{N: 6, ID: "first-case", Title: "Create the first case", State: StepUnavailable, Detail: "Cases arrive with the web app's next version."},
		{N: 7, ID: "invite", Title: "Share the address and invite users", State: StepUnavailable, Detail: "Inviting users arrives with accounts and permissions."},
	}
}
