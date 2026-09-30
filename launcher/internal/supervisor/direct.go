package supervisor

import (
	"context"
	"errors"
	"sync"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
)

// Direct runs services as child processes of the launcher.
type Direct struct {
	specs map[catalog.ServiceID]procs.Spec

	mu      sync.Mutex
	keepers map[catalog.ServiceID]*procs.Keeper
	last    map[catalog.ServiceID]procs.Status
}

// NewDirect returns a supervisor for specs.
func NewDirect(specs map[catalog.ServiceID]procs.Spec) *Direct {
	return &Direct{specs: specs, keepers: map[catalog.ServiceID]*procs.Keeper{}, last: map[catalog.ServiceID]procs.Status{}}
}

// Name implements Supervisor.
func (d *Direct) Name() string { return "Launcher processes" }

// Ready implements Supervisor.
func (d *Direct) Ready(context.Context) error { return nil }

func done(k *procs.Keeper) bool {
	select {
	case <-k.Done():
		return true
	default:
		return false
	}
}

// Start implements Supervisor. Starting a running service does nothing.
func (d *Direct) Start(_ context.Context, id catalog.ServiceID) error {
	spec, ok := d.specs[id]
	if !ok {
		return errors.New("unknown service")
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if k, ok := d.keepers[id]; ok && !done(k) {
		return nil
	}
	d.keepers[id] = procs.Keep(spec, nil)
	return nil
}

// Stop implements Supervisor.
func (d *Direct) Stop(_ context.Context, id catalog.ServiceID) error {
	d.mu.Lock()
	k, ok := d.keepers[id]
	delete(d.keepers, id)
	d.mu.Unlock()
	if !ok {
		return nil
	}
	k.Stop()
	st := k.Status()
	d.mu.Lock()
	d.last[id] = st
	d.mu.Unlock()
	return nil
}

// Info implements Supervisor.
func (d *Direct) Info(_ context.Context, id catalog.ServiceID) (Info, error) {
	d.mu.Lock()
	k, ok := d.keepers[id]
	last, hasLast := d.last[id]
	d.mu.Unlock()
	if !ok {
		if !hasLast {
			last = procs.Status{Service: id}
		}
		last.Running, last.PID, last.StartedAt = false, 0, nil
		return Info{Status: last, Phase: Stopped}, nil
	}
	st := k.Status()
	switch {
	case st.Running:
		return Info{Status: st, Phase: Running}, nil
	case done(k):
		return Info{Status: st, Phase: Stopped}, nil
	default:
		return Info{Status: st, Phase: Starting}, nil
	}
}

// Close stops every service (the launcher is shutting down).
func (d *Direct) Close() {
	for _, s := range catalog.Services {
		_ = d.Stop(context.Background(), s.ID)
	}
}
