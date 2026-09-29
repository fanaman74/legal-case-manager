package status

import (
	"testing"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/docker"
)

func inspect(status, health string, exit int, oom bool) *docker.Inspect {
	var i docker.Inspect
	i.State.Status = status
	i.State.Running = status == "running"
	i.State.ExitCode = exit
	i.State.OOMKilled = oom
	i.State.StartedAt = time.Now().Add(-time.Hour)
	i.Config.Image = "redis:7.4-alpine"
	if health != "" {
		i.State.Health = &struct {
			Status string `json:"Status"`
			Log    []struct {
				End      time.Time `json:"End"`
				ExitCode int       `json:"ExitCode"`
				Output   string    `json:"Output"`
			} `json:"Log"`
		}{Status: health}
	}
	return &i
}

func TestDerive(t *testing.T) {
	svc, _ := catalog.Lookup("queue")
	cases := []struct {
		name    string
		info    *docker.Inspect
		want    State
		problem bool
		fix     bool
	}{
		{"missing", nil, Stopped, false, false},
		{"running no healthcheck", inspect("running", "", 0, false), Running, false, false},
		{"healthy", inspect("running", "healthy", 0, false), Running, false, false},
		{"health starting", inspect("running", "starting", 0, false), Starting, false, false},
		{"unhealthy", inspect("running", "unhealthy", 0, false), Error, true, true},
		{"restarting", inspect("restarting", "", 1, false), Starting, false, false},
		{"created", inspect("created", "", 0, false), Stopped, false, false},
		{"clean exit", inspect("exited", "", 0, false), Stopped, false, false},
		{"stopped by signal", inspect("exited", "", 143, false), Stopped, false, false},
		{"crash", inspect("exited", "", 1, false), Error, true, true},
		{"oom", inspect("exited", "", 137, true), Error, true, true},
		{"dead", inspect("dead", "", 1, false), Error, true, true},
	}
	for _, c := range cases {
		got := Derive(svc, c.info, time.Now())
		if got.State != c.want {
			t.Errorf("%s: state %s want %s", c.name, got.State, c.want)
		}
		if (got.Problem != nil) != c.problem {
			t.Errorf("%s: problem=%v", c.name, got.Problem)
		}
		if got.Problem != nil {
			if got.Problem.What == "" || got.Problem.Why == "" || got.Problem.Next == "" {
				t.Errorf("%s: problem must say what, why and next: %+v", c.name, got.Problem)
			}
			if c.fix && got.Problem.FixAction != catalog.ServiceRestart {
				t.Errorf("%s: expected restart fix", c.name)
			}
		}
	}
}

func TestVersion(t *testing.T) {
	svc, _ := catalog.Lookup("queue")
	i := inspect("running", "", 0, false)
	if v := Derive(svc, i, time.Now()).Version; v != "7.4-alpine" {
		t.Errorf("tag version: %s", v)
	}
	i.Config.Labels = map[string]string{"org.opencontainers.image.version": "24.04"}
	if v := Derive(svc, i, time.Now()).Version; v != "7.4-alpine" {
		t.Errorf("tag must win over a base image label: %s", v)
	}
	i.Config.Image = "casefiles-api"
	i.Config.Labels = map[string]string{"org.opencontainers.image.version": "0.1.0"}
	if v := Derive(svc, i, time.Now()).Version; v != "0.1.0" {
		t.Errorf("label version when untagged: %s", v)
	}
	i.Config.Labels = nil
	i.Config.Image = "localhost:5000/api"
	if v := Derive(svc, i, time.Now()).Version; v != "latest" {
		t.Errorf("registry port is not a tag: %s", v)
	}
}
