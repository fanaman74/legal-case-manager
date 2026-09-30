package server

import (
	"context"
	"errors"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/components"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/supervisor"
)

// Installer checks and installs components; tests supply a fake.
type Installer interface {
	Check() []components.Status
	Needed() []catalog.ComponentID
	Install(ctx context.Context, id catalog.ComponentID, progress components.Progress) error
}

// noComponents is used when no installer is wired in (the prepare command).
type noComponents struct{}

func (noComponents) Check() []components.Status    { return nil }
func (noComponents) Needed() []catalog.ComponentID { return nil }
func (noComponents) Install(context.Context, catalog.ComponentID, components.Progress) error {
	return errors.New("no installer")
}

// componentStatus is the Components list with the running install and the
// last failures laid over it.
func (s *Server) componentStatus() []components.Status {
	list := s.comps.Check()
	op, running := s.ops.current()
	installing := running && (op.Action == catalog.ComponentInstall || op.Action == catalog.InstallMissing)
	s.compMu.Lock()
	defer s.compMu.Unlock()
	for i := range list {
		st := &list[i]
		switch {
		case installing && op.Component == st.ID:
			st.State, st.Detail = components.Installing, op.Message
		case st.State != components.Installed && s.compFail[st.ID] != "":
			st.State, st.Detail = components.Failed, s.compFail[st.ID]
		}
	}
	return list
}

func (s *Server) setCompFail(id catalog.ComponentID, msg string) {
	s.compMu.Lock()
	defer s.compMu.Unlock()
	if msg == "" {
		delete(s.compFail, id)
	} else {
		s.compFail[id] = msg
	}
}

// startInstall begins an install operation for ids and returns a copy of it.
// It returns errBusy if another action is running.
func (s *Server) startInstall(action catalog.ActionName, ids []catalog.ComponentID, actor, ip string) (Operation, error) {
	msg := "Checking what needs installing."
	if len(ids) == 1 {
		if c, ok := catalog.LookupComponent(string(ids[0])); ok {
			msg = "Installing " + c.Name + "."
		}
	}
	op, err := s.ops.start(action, "", actor, msg, s.now())
	if err != nil {
		return Operation{}, err
	}
	started := *op // copy before the goroutine can change it
	go s.runInstall(s.bgCtx, op, action, ids, actor, ip)
	return started, nil
}

// runInstall installs each component in turn. It stops the services that use
// a component while its files are replaced and starts them again afterwards.
// After the first complete install it starts every service, so the app is
// running without anyone pressing a button.
func (s *Server) runInstall(ctx context.Context, op *Operation, action catalog.ActionName, ids []catalog.ComponentID, actor, ip string) {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Hour)
	defer cancel()
	defer s.poller.invalidateChecks()

	for _, id := range ids {
		c, _ := catalog.LookupComponent(string(id))
		entry := audit.Entry{Actor: actor, IP: ip, Action: string(catalog.ComponentInstall), Target: string(id)}
		s.ops.update(op, id, "Installing "+c.Name+".")
		s.poller.refresh(s.bgCtx)

		msg, err := s.installOne(ctx, op, c)
		if err != nil {
			s.log.Error("component install failed", "component", id, "err", err)
			s.setCompFail(id, msg)
			entry.Outcome, entry.Detail = audit.Failed, msg
			_ = s.audit.Append(entry)
			s.ops.update(op, "", msg)
			s.ops.finish(op, false, msg, s.now())
			s.poller.refresh(s.bgCtx)
			return
		}
		s.setCompFail(id, "")
		entry.Outcome = audit.Succeeded
		_ = s.audit.Append(entry)
	}
	s.ops.update(op, "", "Everything is installed.")

	if st := s.cfg.LoadState(); !st.Initialized && len(s.comps.Needed()) == 0 {
		s.ops.update(op, "", "Starting all services.")
		s.poller.refresh(s.bgCtx)
		var all []catalog.ServiceID
		for _, svc := range startOrder() {
			all = append(all, svc.ID)
		}
		if msg, err := s.startServices(ctx, all); err != nil {
			s.ops.finish(op, false, msg, s.now())
			s.poller.refresh(s.bgCtx)
			return
		}
		_, _ = s.updateState(func(st *config.State) { st.Initialized = true })
		_ = s.audit.Append(audit.Entry{Actor: actor, IP: ip, Action: string(catalog.StackStartAll), Outcome: audit.Succeeded, Detail: "started every service after the first install"})
		s.ops.finish(op, true, "Everything is installed and running.", s.now())
		s.poller.refresh(s.bgCtx)
		return
	}
	done := "Everything is installed."
	if len(ids) == 1 {
		c, _ := catalog.LookupComponent(string(ids[0]))
		done = c.Name + " is installed."
	}
	s.ops.finish(op, true, done, s.now())
	s.poller.refresh(s.bgCtx)
}

// installOne installs c and returns a plain message on failure.
func (s *Server) installOne(ctx context.Context, op *Operation, c catalog.Component) (string, error) {
	progress := func(msg string) {
		s.ops.update(op, c.ID, msg)
		s.poller.refresh(s.bgCtx)
	}

	// The embedding model is downloaded by the models service.
	if c.ID == catalog.Model {
		progress("Starting Local AI models to download the embedding model.")
		if msg, err := s.startServices(ctx, []catalog.ServiceID{catalog.Models}); err != nil {
			return "The embedding model needs Local AI models running, and it didn't start. " + msg, err
		}
	}

	// Stop the services using c's files, and remember which were running.
	var restart []catalog.ServiceID
	for _, id := range c.Stops {
		info, err := s.sup.Info(ctx, id)
		if err != nil || info.Phase == supervisor.Stopped {
			continue
		}
		progress("Stopping " + serviceName(id) + " while " + c.Name + " is installed.")
		if err := s.sup.Stop(ctx, id); err != nil {
			return serviceName(id) + " didn't stop, so " + c.Name + " wasn't replaced: " + plain(err) + " Try again, or restart the computer.", err
		}
		restart = append(restart, id)
	}

	err := s.comps.Install(ctx, c.ID, progress)

	if len(restart) > 0 {
		progress("Starting " + serviceName(restart[0]) + " again.")
		if msg, serr := s.startServices(ctx, restart); serr != nil && err == nil {
			return msg, serr
		}
	}
	if err != nil {
		var ie *components.Error
		if errors.As(err, &ie) {
			return ie.What + " " + ie.Next, err
		}
		return c.Name + " couldn't be installed. Try again. If it fails again, download diagnostics.", err
	}
	return "", nil
}

// startServices starts ids in order, waits until they pass their health
// checks, and marks them as wanted after a restart of the computer.
func (s *Server) startServices(ctx context.Context, ids []catalog.ServiceID) (string, error) {
	_, _ = s.updateState(func(st *config.State) {
		set := map[string]bool{}
		for _, id := range st.Wanted {
			set[id] = true
		}
		for _, id := range ids {
			set[string(id)] = true
		}
		st.Wanted = st.Wanted[:0]
		for _, svc := range catalog.Services {
			if set[string(svc.ID)] {
				st.Wanted = append(st.Wanted, string(svc.ID))
			}
		}
	})
	for _, id := range ids {
		if info, err := s.sup.Info(ctx, id); err == nil && info.Phase == supervisor.Running {
			continue
		}
		if err := s.sup.Start(ctx, id); err != nil {
			return serviceName(id) + " didn't start: " + plain(err), err
		}
		s.poller.refresh(s.bgCtx)
	}
	return s.waitReady(ctx, ids)
}

// autoSetup runs when the launcher starts: it installs whatever is missing
// or out of date, then starts every service on a fresh install, or the ones
// that were running before a restart otherwise. Nobody needs to sign in.
func (s *Server) autoSetup(ctx context.Context) {
	needed := s.comps.Needed()
	fresh := !s.cfg.LoadState().Initialized
	if len(needed) == 0 && !fresh {
		s.startWanted(ctx)
		return
	}
	detail := "installing what's missing or out of date"
	if len(needed) == 0 {
		detail = "starting every service for the first time"
	}
	if err := s.audit.Append(audit.Entry{Actor: "launcher", Action: string(catalog.InstallMissing), Outcome: audit.Requested, Detail: detail}); err != nil {
		s.log.Error("audit write failed; not installing", "err", err)
		return
	}
	op, err := s.ops.start(catalog.InstallMissing, "", "launcher", "Checking what needs installing.", s.now())
	if err != nil {
		return
	}
	s.runInstall(ctx, op, catalog.InstallMissing, needed, "launcher", "")
	if !fresh && op.State == OpSucceeded {
		s.startWanted(ctx)
	}
}
