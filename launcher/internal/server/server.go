// Package server is the launcher's HTTPS server: the Control Center page, its
// authenticated JSON API, the live status stream, and the allow-listed action
// endpoint.
package server

import (
	"context"
	"crypto/tls"
	"errors"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/auth"
	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/health"
	"github.com/fanaman74/legal-case-manager/launcher/internal/netguard"
	"github.com/fanaman74/legal-case-manager/launcher/internal/supervisor"
	"github.com/fanaman74/legal-case-manager/launcher/internal/tlsca"
)

// Prober checks a running service's health; tests supply a fake.
type Prober interface {
	Probe(ctx context.Context, id catalog.ServiceID) health.Result
}

// Version is set at build time with -ldflags "-X ...server.Version=...".
var Version = "0.1.0-dev"

// Server wires everything together.
type Server struct {
	cfg    config.Config
	auth   *auth.Store
	audit  *audit.Log
	sup    supervisor.Supervisor
	prober Prober
	web    fs.FS
	log    *slog.Logger
	now    func() time.Time

	// stateMu serialises changes to state.json (LAN switch, wanted services).
	stateMu sync.Mutex

	ops    *operations
	poller *poller

	certMu sync.Mutex
	cert   *tls.Certificate

	lnMu      sync.Mutex
	listeners map[string]*http.Server
	handler   http.Handler
	bgCtx     context.Context
}

// Options are the dependencies New needs.
type Options struct {
	Config     config.Config
	Auth       *auth.Store
	Audit      *audit.Log
	Supervisor supervisor.Supervisor
	// Prober defaults to the real health checks.
	Prober Prober
	Web    fs.FS
	Log    *slog.Logger
}

// New builds a server. Call Run to start listening.
func New(o Options) *Server {
	s := &Server{
		cfg:       o.Config,
		auth:      o.Auth,
		audit:     o.Audit,
		sup:       o.Supervisor,
		prober:    o.Prober,
		web:       o.Web,
		log:       o.Log,
		now:       time.Now,
		ops:       &operations{},
		listeners: map[string]*http.Server{},
		bgCtx:     context.Background(),
	}
	if s.prober == nil {
		s.prober = health.New(o.Config)
	}
	s.poller = newPoller(s)
	s.handler = s.routes()
	return s
}

// Handler exposes the HTTP handler (used by tests).
func (s *Server) Handler() http.Handler { return s.handler }

func (s *Server) auditPath() string { return filepath.Join(s.cfg.LauncherDir(), "audit.jsonl") }

// certNames are the names the server certificate covers.
func (s *Server) certNames() ([]string, []net.IP) {
	names := []string{"localhost"}
	if s.cfg.Hostname != "" {
		names = append(names, s.cfg.Hostname)
	}
	if h, err := os.Hostname(); err == nil && h != "" && h != "localhost" {
		names = append(names, h)
	}
	ips := []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")}
	ips = append(ips, netguard.LANAddresses()...)
	return names, ips
}

// EnsureCertificates creates the CA and server certificate on first run.
func (s *Server) EnsureCertificates() error {
	dir := s.cfg.CertDir()
	if err := tlsca.EnsureCA(dir); err != nil {
		return err
	}
	if _, err := tlsca.ReadServer(dir); err != nil {
		names, ips := s.certNames()
		if err := tlsca.IssueServer(dir, names, ips); err != nil {
			return err
		}
	}
	return s.reloadCert()
}

func (s *Server) renewCert() error {
	names, ips := s.certNames()
	if err := tlsca.IssueServer(s.cfg.CertDir(), names, ips); err != nil {
		return err
	}
	return s.reloadCert()
}

func (s *Server) reloadCert() error {
	dir := s.cfg.CertDir()
	c, err := tls.LoadX509KeyPair(filepath.Join(dir, tlsca.ServerCert), filepath.Join(dir, tlsca.ServerKey))
	if err != nil {
		return err
	}
	s.certMu.Lock()
	s.cert = &c
	s.certMu.Unlock()
	return nil
}

func (s *Server) tlsConfig() *tls.Config {
	return &tls.Config{
		MinVersion: tls.VersionTLS12,
		GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
			s.certMu.Lock()
			defer s.certMu.Unlock()
			if s.cert == nil {
				return nil, errors.New("no certificate loaded")
			}
			return s.cert, nil
		},
	}
}

// wantedAddrs is loopback always, plus private LAN addresses when enabled.
func (s *Server) wantedAddrs() []string {
	port := strconv.Itoa(s.cfg.Port)
	addrs := []string{net.JoinHostPort("127.0.0.1", port)}
	if s.cfg.LoadState().LANEnabled {
		for _, ip := range netguard.LANAddresses() {
			addrs = append(addrs, net.JoinHostPort(ip.String(), port))
		}
	}
	return addrs
}

// reconcileListeners starts and stops listeners to match wantedAddrs. It
// never listens on 0.0.0.0.
func (s *Server) reconcileListeners() error {
	s.lnMu.Lock()
	defer s.lnMu.Unlock()
	want := map[string]bool{}
	var firstErr error
	for _, a := range s.wantedAddrs() {
		want[a] = true
		if _, ok := s.listeners[a]; ok {
			continue
		}
		ln, err := net.Listen("tcp", a)
		if err != nil {
			s.log.Error("listen failed", "addr", a, "err", err)
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		srv := &http.Server{
			Handler:           s.handler,
			TLSConfig:         s.tlsConfig(),
			ReadHeaderTimeout: 10 * time.Second,
			IdleTimeout:       120 * time.Second,
		}
		s.listeners[a] = srv
		go func(addr string) {
			if err := srv.ServeTLS(ln, "", ""); err != nil && !errors.Is(err, http.ErrServerClosed) {
				s.log.Error("server stopped", "addr", addr, "err", err)
			}
		}(a)
		s.log.Info("listening", "addr", "https://"+a)
	}
	for a, srv := range s.listeners {
		if !want[a] {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			_ = srv.Shutdown(ctx)
			cancel()
			delete(s.listeners, a)
			s.log.Info("stopped listening", "addr", a)
		}
	}
	return firstErr
}

// updateState changes state.json under the state lock.
func (s *Server) updateState(f func(*config.State)) (config.State, error) {
	s.stateMu.Lock()
	defer s.stateMu.Unlock()
	st := s.cfg.LoadState()
	f(&st)
	return st, s.cfg.SaveState(st)
}

// startWanted starts the services the Admin had running before the computer
// (or the launcher) restarted. Nobody needs to sign in for this.
func (s *Server) startWanted(ctx context.Context) {
	wanted := map[string]bool{}
	for _, id := range s.cfg.LoadState().Wanted {
		wanted[id] = true
	}
	var ids []catalog.ServiceID
	for _, svc := range startOrder() {
		if wanted[string(svc.ID)] {
			ids = append(ids, svc.ID)
		}
	}
	if len(ids) == 0 {
		return
	}
	entry := audit.Entry{Actor: "launcher", Action: string(catalog.StackStartAll), Outcome: audit.Requested, Detail: "starting the services that were running before the restart"}
	if err := s.audit.Append(entry); err != nil {
		s.log.Error("audit write failed; not starting services", "err", err)
		return
	}
	op, err := s.ops.start(catalog.StackStartAll, "", "launcher", "Starting the services that were running before the restart.", s.now())
	if err != nil {
		return
	}
	s.runServices(ctx, op, catalog.StackStartAll, ids, "launcher", "", "The services")
}

// Run starts the poller and listeners and blocks until ctx is done.
func (s *Server) Run(ctx context.Context) error {
	s.bgCtx = ctx
	go s.poller.run(ctx)
	go s.startWanted(ctx)
	if err := s.reconcileListeners(); err != nil {
		s.lnMu.Lock()
		n := len(s.listeners)
		s.lnMu.Unlock()
		if n == 0 {
			return err
		}
	}
	// LAN addresses can change (DHCP, Wi-Fi); follow them.
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			s.lnMu.Lock()
			for a, srv := range s.listeners {
				c, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				_ = srv.Shutdown(c)
				cancel()
				delete(s.listeners, a)
			}
			s.lnMu.Unlock()
			return nil
		case <-t.C:
			_ = s.reconcileListeners()
		}
	}
}
