// Command launcher is the always-on service that serves the Control Center
// and starts, stops and monitors the Case File Manager services.
//
//	launcher run                 run in the foreground
//	launcher service install     register as a Windows service (Windows only)
//	launcher service uninstall
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
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/auth"
	"github.com/fanaman74/legal-case-manager/launcher/internal/compose"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/docker"
	"github.com/fanaman74/legal-case-manager/launcher/internal/redact"
	"github.com/fanaman74/legal-case-manager/launcher/internal/server"
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

	// Started by the Windows service manager: no arguments are passed.
	if winsvc.IsService() {
		return winsvc.Run(func(ctx context.Context) error { return run(ctx, *cfgPath) })
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

	dockerBin := cfg.Docker
	if dockerBin == "" {
		if p, err := exec.LookPath("docker"); err == nil {
			dockerBin = p
		} else {
			dockerBin = defaultDocker()
		}
	}

	srv := server.New(server.Options{
		Config: cfg,
		Auth:   store,
		Audit:  al,
		Engine: docker.New(cfg.Project),
		Runner: compose.ExecRunner{Docker: dockerBin},
		Web:    web.FS(),
		Log:    log,
	})
	if err := srv.EnsureCertificates(); err != nil {
		return fmt.Errorf("prepare HTTPS certificate: %w", err)
	}
	_ = al.Append(audit.Entry{Actor: "system", IP: "local", Action: "launcher.start", Outcome: audit.Succeeded, Detail: "version " + server.Version})
	log.Info("launcher starting", "version", server.Version, "data", cfg.DataDir, "compose", cfg.ComposeFile)
	err = srv.Run(ctx)
	_ = al.Append(audit.Entry{Actor: "system", IP: "local", Action: "launcher.stop", Outcome: audit.Succeeded})
	return err
}

func defaultDocker() string {
	if strings.HasPrefix(strings.ToLower(os.Getenv("OS")), "windows") {
		return `C:\Program Files\Docker\Docker\resources\bin\docker.exe`
	}
	return "docker"
}
