package health

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
)

func testConfig(t *testing.T) config.Config {
	cfg, err := config.Load(filepath.Join(t.TempDir(), "launcher.json"))
	if err != nil {
		t.Fatal(err)
	}
	return cfg
}

func TestWorkerHeartbeat(t *testing.T) {
	cfg := testConfig(t)
	p := New(cfg)
	if r := p.Probe(context.Background(), catalog.Worker); r.OK {
		t.Fatal("no heartbeat should fail")
	}
	hb := procs.HeartbeatFile(cfg)
	_ = os.MkdirAll(filepath.Dir(hb), 0o755)
	_ = os.WriteFile(hb, []byte(fmt.Sprintf("%d 0.2.0\n", time.Now().Unix())), 0o644)
	if r := p.Probe(context.Background(), catalog.Worker); !r.OK || r.Version != "0.2.0" {
		t.Errorf("fresh heartbeat: %+v", r)
	}
	_ = os.WriteFile(hb, []byte(fmt.Sprintf("%d 0.2.0\n", time.Now().Add(-time.Hour).Unix())), 0o644)
	if r := p.Probe(context.Background(), catalog.Worker); r.OK || r.Detail == "" {
		t.Errorf("stale heartbeat: %+v", r)
	}
}

func TestModelsVersion(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/version" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"version":"0.34.4"}`))
	}))
	defer srv.Close()
	_, port, _ := net.SplitHostPort(srv.Listener.Addr().String())
	cfg := testConfig(t)
	cfg.ModelsPort, _ = strconv.Atoi(port)
	r := New(cfg).Probe(context.Background(), catalog.Models)
	if !r.OK || r.Version != "0.34.4" {
		t.Errorf("models: %+v", r)
	}
	srv.Close()
	if r := New(cfg).Probe(context.Background(), catalog.Models); r.OK {
		t.Error("closed server should fail")
	}
}
