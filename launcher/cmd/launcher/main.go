// Command launcher is the always-on service that serves the Control Center
// and starts, stops and monitors the Case File Manager services.
//
//	launcher run                 run in the foreground
//	launcher host <service>      run one app service (started by Windows)
//	launcher service install     register the Windows services (Windows only)
//	launcher service uninstall
//	launcher prepare             create the certificates and setup code (installer)
//	launcher setup-code          print the one-time setup code, if setup isn't done
//	launcher verify-audit        check the audit log's hash chain
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/auth"
	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
	"github.com/fanaman74/legal-case-manager/launcher/internal/redact"
	"github.com/fanaman74/legal-case-manager/launcher/internal/server"
	"github.com/fanaman74/legal-case-manager/launcher/internal/supervisor"
	"github.com/fanaman74/legal-case-manager/launcher/internal/web"
	"github.com/fanaman74/legal-case-manager/launcher/internal/winsvc"
)

func main() {
	if err := dispatch(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "launcher:", err)
		os.Exit(1)
	}
}

func dispatch(args []string) error {
	fs := flag.NewFlagSet("launcher", flag.ContinueOnError)
	cfgPath := fs.String("config", config.DefaultPath(), "path to launcher.json")
	if err := fs.Parse(args); err != nil {
		return err
	}
	rest := fs.Args()

	// A service host for one app process: `host <service>`.
	if len(rest) == 2 && rest[0] == "host" {
		svc, ok := catalog.Lookup(rest[1])
		if !ok {
			return fmt.Errorf("unknown service %q", rest[1])
		}
		hostFn := func(ctx context.Context) error { return host(ctx, *cfgPath, svc.ID) }
		if winsvc.IsService() {
			return winsvc.Run(svc.WindowsName(), hostFn)
		}
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		return hostFn(ctx)
	}
	// The launcher itself, started by the Windows service manager.
	if winsvc.IsService() {
		return winsvc.Run(winsvc.Name, func(ctx context.Context) error { return run(ctx, *cfgPath) })
	}
	if len(rest) == 0 {
		rest = []string{"run"}
	}
	switch rest[0] {
	case "run":
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		return run(ctx, *cfgPath)
	case "service":
		if len(rest) < 2 {
			return errors.New("usage: launcher service install|uninstall")
		}
		abs, _ := filepath.Abs(*cfgPath)
		switch rest[1] {
		case "install":
			return winsvc.Install(abs)
		case "uninstall":
			return winsvc.Uninstall()
		}
		return errors.New("usage: launcher service install|uninstall")
	case "prepare":
		// Run by the installer before it sets file permissions, so the
		// certificate files exist and can be shared with the web app.
		cfg, err := config.Load(*cfgPath)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(cfg.LauncherDir(), 0o700); err != nil {
			return err
		}
		if _, _, err := auth.Open(cfg.LauncherDir()); err != nil {
			return err
		}
		srv := server.New(server.Options{Config: cfg, Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
		if err := srv.EnsureCertificates(); err != nil {
			return fmt.Errorf("create certificates: %w", err)
		}
		for _, svc := range catalog.Services {
			if err := os.MkdirAll(procs.ServiceLogDir(cfg, svc.ID), 0o750); err != nil {
				return err
			}
		}
		fmt.Println("Prepared certificates, setup code and log folders.")
		return nil
	case "setup-code":
		cfg, err := config.Load(*cfgPath)
		if err != nil {
			return err
		}
		store, code, err := auth.Open(cfg.LauncherDir())
		if err != nil {
			return err
		}
		if store.HasAdmin() {
			fmt.Println("Setup is complete. Sign in with the Admin account.")
			return nil
		}
		if code == "" {
			b, err := os.ReadFile(store.SetupCodePath())
			if err != nil {
				return fmt.Errorf("the setup code file is missing: %w", err)
			}
			fmt.Print(string(b))
			return nil
		}
		fmt.Println(code)
		return nil
	case "verify-audit":
		cfg, err := config.Load(*cfgPath)
		if err != nil {
			return err
		}
		n, err := audit.Verify(filepath.Join(cfg.LauncherDir(), "audit.jsonl"))
		if err != nil {
			return err
		}
		fmt.Printf("Audit log intact: %d entries.\n", n)
		return nil
	}
	return fmt.Errorf("unknown command %q", rest[0])
}

// redactingWriter scrubs secrets from the launcher's own log output.
type redactingWriter struct{ w io.Writer }

func (r redactingWriter) Write(p []byte) (int, error) {
	if _, err := io.WriteString(r.w, redact.String(string(p))); err != nil {
		return 0, err
	}
	return len(p), nil
}

func run(ctx context.Context, cfgPath string) error {
	cfg, err := config.Load(cfgPath)
	if err != nil {
		return fmt.Errorf("read config: %w", err)
	}
	if err := os.MkdirAll(cfg.LauncherDir(), 0o700); err != nil {
		return err
	}
	logPath := filepath.Join(cfg.LauncherDir(), "launcher.log")
	if st, err := os.Stat(logPath); err == nil && st.Size() > 10<<20 {
		_ = os.Rename(logPath, logPath+".1")
	}
	lf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	defer lf.Close()
	log := slog.New(slog.NewTextHandler(redactingWriter{io.MultiWriter(os.Stderr, lf)}, nil))

	store, code, err := auth.Open(cfg.LauncherDir())
	if err != nil {
		return fmt.Errorf("open credentials: %w", err)
	}
	if code != "" {
		// Printed for the installer window; also in setup-code.txt.
		fmt.Fprintf(os.Stderr, "\nSetup code: %s\n(also saved to %s)\n\n", code, store.SetupCodePath())
		log.Info("setup code issued", "file", store.SetupCodePath())
	}
	al, err := audit.Open(filepath.Join(cfg.LauncherDir(), "audit.jsonl"))
	if err != nil {
		return fmt.Errorf("open audit log: %w", err)
	}

	var sup supervisor.Supervisor
	switch cfg.Supervisor {
	case "windows":
		if sup, err = supervisor.NewWindows(cfg); err != nil {
			return err
		}
	case "direct":
		d := supervisor.NewDirect(procs.Specs(cfg, server.Version))
		defer d.Close() // child processes stop with the launcher
		sup = d
	default:
		return fmt.Errorf(`unknown supervisor %q in launcher.json; use "windows" or "direct"`, cfg.Supervisor)
	}

	srv := server.New(server.Options{
		Config:     cfg,
		Auth:       store,
		Audit:      al,
		Supervisor: sup,
		Web:        web.FS(),
		Log:        log,
	})
	if err := srv.EnsureCertificates(); err != nil {
		return fmt.Errorf("prepare HTTPS certificate: %w", err)
	}
	_ = al.Append(audit.Entry{Actor: "system", IP: "local", Action: "launcher.start", Outcome: audit.Succeeded, Detail: "version " + server.Version})
	log.Info("launcher starting", "version", server.Version, "data", cfg.DataDir, "supervisor", sup.Name())
	err = srv.Run(ctx)
	_ = al.Append(audit.Entry{Actor: "system", IP: "local", Action: "launcher.stop", Outcome: audit.Succeeded})
	return err
}

// host runs one app process for the Windows service manager (or in the
// foreground for testing), restarting it after crashes and recording its
// state where the launcher can read it.
func host(ctx context.Context, cfgPath string, id catalog.ServiceID) error {
	cfg, err := config.Load(cfgPath)
	if err != nil {
		return fmt.Errorf("read config: %w", err)
	}
	spec := procs.Specs(cfg, server.Version)[id]
	statusPath := procs.StatusFile(cfg, id)
	k := procs.Keep(spec, func(st procs.Status) { _ = procs.WriteStatus(statusPath, st) })
	select {
	case <-ctx.Done():
		k.Stop()
	case <-k.Done():
		// Gave up after repeated crashes, or the program is missing. Stop
		// cleanly so Windows doesn't restart it; the launcher shows why.
	}
	_ = procs.WriteStatus(statusPath, k.Status())
	return nil
}
