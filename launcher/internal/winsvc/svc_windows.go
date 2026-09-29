//go:build windows

// Package winsvc runs the launcher, and the hosts for each app process, as
// Windows services.
package winsvc

import (
	"context"
	"errors"
	"fmt"
	"os"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

// Name is the launcher's Windows service name.
const Name = "CaseFileManagerLauncher"

// IsService reports whether the process was started by the service manager.
func IsService() bool {
	ok, err := svc.IsWindowsService()
	return err == nil && ok
}

type handler struct {
	run func(ctx context.Context) error
}

func (h handler) Execute(_ []string, req <-chan svc.ChangeRequest, st chan<- svc.Status) (bool, uint32) {
	st <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- h.run(ctx) }()
	st <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for {
		select {
		case err := <-done:
			cancel()
			if err != nil {
				return true, 1
			}
			return false, 0
		case c := <-req:
			switch c.Cmd {
			case svc.Interrogate:
				st <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				st <- svc.Status{State: svc.StopPending}
				cancel()
				select {
				case <-done:
				case <-time.After(20 * time.Second):
				}
				return false, 0
			}
		}
	}
}

// Run hands control to the service manager as the named service.
func Run(name string, run func(ctx context.Context) error) error {
	return svc.Run(name, handler{run: run})
}

var recovery = []mgr.RecoveryAction{
	{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
	{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
	{Type: mgr.ServiceRestart, Delay: 60 * time.Second},
}

// ensure creates the service, or updates it if it already exists, so running
// the installer again repairs an installation.
func ensure(m *mgr.Mgr, name, exe string, c mgr.Config, args ...string) error {
	s, err := m.OpenService(name)
	if err != nil {
		s, err = m.CreateService(name, exe, c, args...)
		if err != nil {
			return fmt.Errorf("create %s: %w", name, err)
		}
	} else {
		cur, err := s.Config()
		if err != nil {
			s.Close()
			return err
		}
		cur.BinaryPathName = syscall.EscapeArg(exe)
		for _, a := range args {
			cur.BinaryPathName += " " + syscall.EscapeArg(a)
		}
		cur.DisplayName, cur.Description = c.DisplayName, c.Description
		cur.StartType, cur.DelayedAutoStart = c.StartType, c.DelayedAutoStart
		cur.ServiceStartName, cur.Password = c.ServiceStartName, c.Password
		cur.SidType = c.SidType
		if err := s.UpdateConfig(cur); err != nil {
			s.Close()
			return fmt.Errorf("update %s: %w", name, err)
		}
	}
	defer s.Close()
	return s.SetRecoveryActions(recovery, 24*60*60)
}

// Install registers the launcher (LocalSystem, starts at boot) and one host
// service per app process. Each host runs under its own virtual account,
// NT SERVICE\CaseFiles-<id>, which has no password, can't sign in, and gets
// only the file permissions the installer grants it. The launcher starts
// the hosts itself, so they are set to manual start.
func Install(configPath string) error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to the service manager (run as Administrator): %w", err)
	}
	defer m.Disconnect()
	if err := ensure(m, Name, exe, mgr.Config{
		DisplayName:      "Case File Manager launcher",
		Description:      "Serves the Control Center and starts the Case File Manager services.",
		StartType:        mgr.StartAutomatic,
		DelayedAutoStart: true,
		ServiceStartName: "LocalSystem",
	}, "--config", configPath); err != nil {
		return err
	}
	for _, sv := range catalog.Services {
		name := sv.WindowsName()
		if err := ensure(m, name, exe, mgr.Config{
			DisplayName:      "Case File Manager " + sv.Name,
			Description:      sv.Purpose + " Started and stopped from the Control Center.",
			StartType:        mgr.StartManual,
			ServiceStartName: `NT SERVICE\` + name,
			SidType:          windows.SERVICE_SID_TYPE_UNRESTRICTED,
		}, "--config", configPath, "host", string(sv.ID)); err != nil {
			return err
		}
	}
	return nil
}

// Uninstall stops and removes every Case File Manager service.
func Uninstall() error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	names := []string{Name}
	for _, sv := range catalog.Services {
		names = append(names, sv.WindowsName())
	}
	var errs []error
	for _, name := range names {
		s, err := m.OpenService(name)
		if err != nil {
			continue
		}
		if st, err := s.Control(svc.Stop); err == nil {
			for i := 0; i < 60 && st.State != svc.Stopped; i++ {
				time.Sleep(500 * time.Millisecond)
				if st, err = s.Query(); err != nil {
					break
				}
			}
		}
		if err := s.Delete(); err != nil {
			errs = append(errs, fmt.Errorf("remove %s: %w", name, err))
		}
		s.Close()
	}
	return errors.Join(errs...)
}
