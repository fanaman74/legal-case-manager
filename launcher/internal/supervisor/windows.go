//go:build windows

package supervisor

import (
	"context"
	"errors"
	"fmt"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
)

// Windows controls the per-service Windows services through the SCM.
type Windows struct {
	cfg config.Config
}

// NewWindows returns the SCM supervisor.
func NewWindows(cfg config.Config) (Supervisor, error) { return &Windows{cfg: cfg}, nil }

// Name implements Supervisor.
func (w *Windows) Name() string { return "Windows services" }

var errNotRegistered = errors.New("The app services aren't registered with Windows. Double-click Setup.exe from the install zip again. It repairs the installation and keeps your cases.")

func (w *Windows) open(id catalog.ServiceID) (*mgr.Mgr, *mgr.Service, error) {
	s, ok := catalog.Lookup(string(id))
	if !ok {
		return nil, nil, errors.New("unknown service")
	}
	m, err := mgr.Connect()
	if err != nil {
		return nil, nil, fmt.Errorf("can't reach the Windows service manager: %w", err)
	}
	h, err := m.OpenService(s.WindowsName())
	if err != nil {
		m.Disconnect()
		return nil, nil, errNotRegistered
	}
	return m, h, nil
}

// Ready implements Supervisor.
func (w *Windows) Ready(context.Context) error {
	for _, s := range catalog.Services {
		m, h, err := w.open(s.ID)
		if err != nil {
			return err
		}
		h.Close()
		m.Disconnect()
	}
	return nil
}

// Start implements Supervisor.
func (w *Windows) Start(_ context.Context, id catalog.ServiceID) error {
	m, h, err := w.open(id)
	if err != nil {
		return err
	}
	defer m.Disconnect()
	defer h.Close()
	if err := h.Start(); err != nil && !errors.Is(err, windows.ERROR_SERVICE_ALREADY_RUNNING) {
		return err
	}
	return nil
}

// Stop implements Supervisor and waits for the service to stop.
func (w *Windows) Stop(ctx context.Context, id catalog.ServiceID) error {
	m, h, err := w.open(id)
	if err != nil {
		return err
	}
	defer m.Disconnect()
	defer h.Close()
	st, err := h.Control(svc.Stop)
	if err != nil && !errors.Is(err, windows.ERROR_SERVICE_NOT_ACTIVE) {
		return err
	}
	deadline := time.Now().Add(30 * time.Second)
	for st.State != svc.Stopped {
		if time.Now().After(deadline) {
			return errors.New("it didn't stop within 30 seconds")
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(300 * time.Millisecond):
		}
		if st, err = h.Query(); err != nil {
			return err
		}
	}
	return nil
}

// Info implements Supervisor.
func (w *Windows) Info(_ context.Context, id catalog.ServiceID) (Info, error) {
	m, h, err := w.open(id)
	if err != nil {
		return Info{}, err
	}
	defer m.Disconnect()
	defer h.Close()
	q, err := h.Query()
	if err != nil {
		return Info{}, err
	}
	st, _ := procs.ReadStatus(procs.StatusFile(w.cfg, id))
	st.Service = id
	out := Info{Status: st}
	switch q.State {
	case svc.Running:
		out.Phase = Running
		if !st.Running {
			out.Phase = Starting // host is up, app between restarts
		}
	case svc.StartPending, svc.ContinuePending:
		out.Phase = Starting
	case svc.StopPending, svc.PausePending, svc.Paused:
		out.Phase = Stopping
	default:
		out.Phase = Stopped
		out.Running, out.PID, out.StartedAt = false, 0, nil
	}
	return out, nil
}
