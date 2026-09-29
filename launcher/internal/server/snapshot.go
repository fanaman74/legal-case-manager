package server

import (
	"context"
	"fmt"
	"net"
	"strconv"
	"sync"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/checks"
	"github.com/fanaman74/legal-case-manager/launcher/internal/docker"
	"github.com/fanaman74/legal-case-manager/launcher/internal/netguard"
	"github.com/fanaman74/legal-case-manager/launcher/internal/status"
)

// Engine is the read-only Docker API the poller needs; tests supply a fake.
type Engine interface {
	Version(ctx context.Context) (docker.Version, error)
	List(ctx context.Context) ([]docker.Container, error)
	Inspect(ctx context.Context, id string) (docker.Inspect, error)
	Stats(ctx context.Context, id string) (docker.Usage, error)
	Logs(ctx context.Context, id string, tail int) ([]docker.LogLine, error)
}

// DockerState is the engine's reachability.
type DockerState struct {
	OK      bool            `json:"ok"`
	Version string          `json:"version,omitempty"`
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
	GeneratedAt time.Time        `json:"generatedAt"`
	Launcher    string           `json:"launcherVersion"`
	Docker      DockerState      `json:"docker"`
	Services    []status.Service `json:"services"`
	Checks      []checks.Check   `json:"checks"`
	LAN         LAN              `json:"lan"`
	Operations  []Operation      `json:"operations"`
	Wizard      []WizardStep     `json:"wizard"`
	HasAdmin    bool             `json:"hasAdmin"`
}

type poller struct {
	s  *Server
	mu sync.RWMutex
	// latest is replaced wholesale; readers never see partial updates.
	latest Snapshot
	// usage is refreshed less often because each sample takes ~1s.
	usage     map[catalog.ServiceID]docker.Usage
	usageAt   time.Time
	restarts  map[catalog.ServiceID]int
	autoAt    map[catalog.ServiceID]time.Time
	checksAt  time.Time
	checkList []checks.Check
	subs      map[chan Snapshot]struct{}
}

func newPoller(s *Server) *poller {
	return &poller{
		s:        s,
		usage:    map[catalog.ServiceID]docker.Usage{},
		restarts: map[catalog.ServiceID]int{},
		autoAt:   map[catalog.ServiceID]time.Time{},
		subs:     map[chan Snapshot]struct{}{},
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
	p.mu.Unlock()
}

func (p *poller) get() Snapshot {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.latest
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

	v, verr := s.engine.Version(ctx)
	snap.Docker = DockerState{OK: verr == nil, Version: v.Version}
	infos := map[catalog.ServiceID]*docker.Inspect{}
	ids := map[catalog.ServiceID]string{}
	if verr != nil {
		pr := status.DockerDown
		snap.Docker.Problem = &pr
	} else if list, err := s.engine.List(ctx); err == nil {
		for _, ct := range list {
			svc, ok := catalog.ByComposeService(ct.ComposeService())
			if !ok {
				continue
			}
			info, err := s.engine.Inspect(ctx, ct.ID)
			if err != nil {
				continue
			}
			infos[svc.ID] = &info
			ids[svc.ID] = ct.ID
		}
	}

	p.refreshUsage(ctx, ids, infos, now)

	busy := s.ops.busyServices()
	p.mu.Lock()
	for _, svc := range catalog.Services {
		st := status.Derive(svc, infos[svc.ID], now)
		if verr != nil {
			st.Detail = "Unknown until Docker is running"
		}
		if u, ok := p.usage[svc.ID]; ok && st.State == status.Running {
			u := u
			st.Usage = &u
		}
		// Docker restarted the container on its own: say so.
		if prev, seen := p.restarts[svc.ID]; seen && st.RestartCount > prev {
			p.autoAt[svc.ID] = now
		}
		p.restarts[svc.ID] = st.RestartCount
		if t, ok := p.autoAt[svc.ID]; ok {
			t := t
			st.AutoRestart = &t
		}
		if label, ok := busy[svc.ID]; ok && st.State != status.Error {
			st.State, st.Detail = status.Starting, label
		}
		snap.Services = append(snap.Services, st)
	}
	p.mu.Unlock()

	snap.Checks = p.systemChecks(snap, verr, now)
	snap.LAN = s.lanInfo()
	snap.Operations = s.ops.list()
	snap.Wizard = wizard(snap)

	p.mu.Lock()
	p.latest = snap
	for ch := range p.subs {
		select {
		case ch <- snap:
		default:
		}
	}
	p.mu.Unlock()
}

func (p *poller) refreshUsage(ctx context.Context, ids map[catalog.ServiceID]string, infos map[catalog.ServiceID]*docker.Inspect, now time.Time) {
	p.mu.RLock()
	fresh := now.Sub(p.usageAt) < 5*time.Second
	p.mu.RUnlock()
	if fresh {
		return
	}
	var wg sync.WaitGroup
	var mu sync.Mutex
	next := map[catalog.ServiceID]docker.Usage{}
	for id, cid := range ids {
		if !infos[id].State.Running {
			continue
		}
		wg.Add(1)
		go func(id catalog.ServiceID, cid string) {
			defer wg.Done()
			if u, err := p.s.engine.Stats(ctx, cid); err == nil {
				mu.Lock()
				next[id] = u
				mu.Unlock()
			}
		}(id, cid)
	}
	wg.Wait()
	p.mu.Lock()
	p.usage, p.usageAt = next, now
	p.mu.Unlock()
}

func (p *poller) systemChecks(snap Snapshot, verr error, now time.Time) []checks.Check {
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
	byID["docker"] = checks.Docker(snap.Docker.Version, verr)
	byID["model"] = checks.Model(s.cfg.DataDir, s.cfg.EmbeddingModel)
	byID["ocr"] = checks.OCR(svc(catalog.OCR) == status.Running)

	order := []string{"docker", "disk", "port", "cert", "model", "ocr", "database", "vectors", "audit"}
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
	for _, id := range []string{"docker", "disk", "port"} {
		if check(id) == checks.Fail {
			failed++
		}
	}
	if failed == 0 {
		pre.State, pre.Detail = StepDone, "Docker is running, there is enough disk space, and the web app's port is free."
	} else {
		pre.State, pre.Detail = StepFailed, fmt.Sprintf("%d check(s) need attention.", failed)
	}

	svc := WizardStep{N: 2, ID: "services", Title: "Start the services"}
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

	admin := WizardStep{N: 3, ID: "admin", Title: "Create the Admin account"}
	if snap.HasAdmin {
		admin.State, admin.Detail = StepDone, "The Admin account exists."
	} else {
		admin.State, admin.Detail = StepTodo, "Choose the username and password you'll use to sign in."
	}

	model := WizardStep{N: 4, ID: "embeddings", Title: "Choose the embedding model"}
	if check("model") == checks.Pass {
		model.State, model.Detail = StepDone, "The embedding model is downloaded."
	} else {
		model.State, model.Detail = StepUnavailable, "Model download isn't available in this version yet. It arrives with search indexing."
	}

	return []WizardStep{
		pre, svc, admin, model,
		{N: 5, ID: "chat", Title: "Connect a chat provider (optional)", State: StepUnavailable, Detail: "Provider settings arrive with the AI features."},
		{N: 6, ID: "first-case", Title: "Create the first case", State: StepUnavailable, Detail: "Cases arrive with the web app's next version."},
		{N: 7, ID: "invite", Title: "Share the address and invite users", State: StepUnavailable, Detail: "Inviting users arrives with accounts and permissions."},
	}
}
