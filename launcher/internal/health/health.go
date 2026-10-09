// Package health checks that each running service actually works: the web app
// and the local AI models answer on their loopback addresses, and the worker
// keeps writing its heartbeat.
package health

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
)

// HeartbeatMaxAge is how old the worker's heartbeat may be.
const HeartbeatMaxAge = 30 * time.Second

// Result is one health check.
type Result struct {
	OK      bool
	Version string
	// Detail says what failed, in plain words.
	Detail string
	At     time.Time
}

// Prober runs the checks.
type Prober struct {
	cfg config.Config
	now func() time.Time

	mu     sync.Mutex
	caMod  time.Time
	client *http.Client
}

// New returns a prober for cfg.
func New(cfg config.Config) *Prober { return &Prober{cfg: cfg, now: time.Now} }

// Probe checks one service.
func (p *Prober) Probe(ctx context.Context, id catalog.ServiceID) Result {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	r := Result{At: p.now()}
	switch id {
	case catalog.API:
		r.Version, r.Detail = p.getVersion(ctx, "https://127.0.0.1:"+strconv.Itoa(p.cfg.AppPort)+"/health")
	case catalog.Models:
		r.Version, r.Detail = p.getVersion(ctx, procs.ModelsURL(p.cfg)+"/api/version")
	case catalog.Worker:
		r.Version, r.Detail = p.heartbeat()
	default:
		r.Detail = "unknown service"
	}
	r.OK = r.Detail == ""
	return r
}

// httpClient trusts only the launcher's own CA, reloading it after renewal.
func (p *Prober) httpClient() *http.Client {
	caPath := filepath.Join(p.cfg.CertsDir, "ca.crt")
	st, err := os.Stat(caPath)
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.client != nil && (err != nil || st.ModTime().Equal(p.caMod)) {
		return p.client
	}
	pool := x509.NewCertPool()
	if b, err := os.ReadFile(caPath); err == nil {
		pool.AppendCertsFromPEM(b)
	}
	if st != nil {
		p.caMod = st.ModTime()
	}
	p.client = &http.Client{
		Transport: &http.Transport{
			TLSClientConfig:   &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12},
			Proxy:             nil, // loopback only; never through a proxy
			DisableKeepAlives: true,
		},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	return p.client
}

func (p *Prober) getVersion(ctx context.Context, url string) (version, problem string) {
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	resp, err := p.httpClient().Do(req)
	if err != nil {
		if strings.Contains(err.Error(), "certificate") {
			return "", "its certificate isn't trusted (renew it in Network)"
		}
		return "", "it isn't answering yet"
	}
	defer resp.Body.Close()
	var body struct {
		Version string `json:"version"`
	}
	_ = json.NewDecoder(io.LimitReader(resp.Body, 4096)).Decode(&body)
	if resp.StatusCode != http.StatusOK {
		return body.Version, fmt.Sprintf("its health check answered %d", resp.StatusCode)
	}
	return body.Version, ""
}

func (p *Prober) heartbeat() (version, problem string) {
	b, err := os.ReadFile(procs.HeartbeatFile(p.cfg))
	if err != nil {
		return "", "it hasn't reported in yet"
	}
	f := strings.Fields(string(b))
	if len(f) == 0 {
		return "", "its heartbeat file is empty"
	}
	ts, err := strconv.ParseInt(f[0], 10, 64)
	if err != nil {
		return "", "its heartbeat file is unreadable"
	}
	if len(f) > 1 {
		version = f[1]
	}
	if age := p.now().Sub(time.Unix(ts, 0)); age > HeartbeatMaxAge {
		return version, fmt.Sprintf("it last reported in %s ago", age.Round(time.Second))
	}
	return version, ""
}
