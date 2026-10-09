package status

import (
	"testing"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/health"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
	"github.com/fanaman74/legal-case-manager/launcher/internal/supervisor"
)

func TestDerive(t *testing.T) {
	svc, _ := catalog.Lookup("worker")
	now := time.Now()
	old := now.Add(-time.Hour)
	fresh := now.Add(-5 * time.Second)
	ok := &health.Result{OK: true, Version: "0.2.0", At: now}
	bad := &health.Result{OK: false, Detail: "it last reported in 2m0s ago", At: now}
	info := func(phase supervisor.Phase, f func(*procs.Status)) supervisor.Info {
		st := procs.Status{Service: svc.ID}
		if f != nil {
			f(&st)
		}
		return supervisor.Info{Status: st, Phase: phase}
	}
	running := func(started time.Time) func(*procs.Status) {
		return func(s *procs.Status) { s.Running, s.PID, s.StartedAt = true, 42, &started }
	}
	cases := []struct {
		name    string
		info    supervisor.Info
		h       *health.Result
		want    State
		problem bool
		fix     bool
	}{
		{"never started", info(supervisor.Stopped, nil), nil, Stopped, false, false},
		{"stopped after running", info(supervisor.Stopped, func(s *procs.Status) { s.LastExit = &procs.Exit{Code: 1} }), nil, Stopped, false, false},
		{"starting", info(supervisor.Starting, nil), nil, Starting, false, false},
		{"stopping", info(supervisor.Stopping, nil), nil, Starting, false, false},
		{"running healthy", info(supervisor.Running, running(old)), ok, Running, false, false},
		{"running, grace period", info(supervisor.Running, running(fresh)), bad, Starting, false, false},
		{"running, not responding", info(supervisor.Running, running(old)), bad, Error, true, true},
		{"running, never probed", info(supervisor.Running, running(old)), nil, Error, true, true},
		{"missing program", info(supervisor.Stopped, func(s *procs.Status) { s.Missing, s.GaveUp = true, true }), nil, Error, true, false},
		{"start error", info(supervisor.Stopped, func(s *procs.Status) { s.GaveUp, s.StartErr = true, "access denied" }), nil, Error, true, true},
		{"crash loop", info(supervisor.Stopped, func(s *procs.Status) { s.GaveUp, s.Restarts, s.LastExit = true, 4, &procs.Exit{Code: 3} }), nil, Error, true, true},
		{"unknown phase", info("weird", nil), nil, Error, true, true},
	}
	for _, c := range cases {
		got := Derive(svc, c.info, c.h, now)
		if got.State != c.want {
			t.Errorf("%s: state %s want %s", c.name, got.State, c.want)
		}
		if (got.Problem != nil) != c.problem {
			t.Errorf("%s: problem=%+v", c.name, got.Problem)
		}
		if got.Problem != nil {
			if got.Problem.What == "" || got.Problem.Why == "" || got.Problem.Next == "" {
				t.Errorf("%s: problem must say what, why and next: %+v", c.name, got.Problem)
			}
			if c.fix != (got.Problem.FixAction == catalog.ServiceRestart) {
				t.Errorf("%s: fix action %q", c.name, got.Problem.FixAction)
			}
		}
	}
}

func TestDeriveDetails(t *testing.T) {
	svc, _ := catalog.Lookup("api")
	now := time.Now()
	started := now.Add(-time.Hour)
	restarted := now.Add(-time.Minute)
	got := Derive(svc, supervisor.Info{Phase: supervisor.Running, Status: procs.Status{
		Running: true, StartedAt: &started, Restarts: 2, LastRestartAt: &restarted,
	}}, &health.Result{OK: true, Version: "0.2.0", At: now}, now)
	if got.Version != "0.2.0" || got.RestartCount != 2 || got.AutoRestart == nil || got.StartedAt == nil {
		t.Errorf("details: %+v", got)
	}
	stopped := Derive(svc, supervisor.Info{Phase: supervisor.Stopped, Status: procs.Status{Restarts: 2, LastRestartAt: &restarted, LastExit: &procs.Exit{}}}, nil, now)
	if stopped.AutoRestart != nil {
		t.Error("a service the Admin stopped shouldn't still show the crash-restart note")
	}
	if d := Derive(svc, supervisor.Info{Phase: supervisor.Starting, Status: procs.Status{Restarts: 1}}, nil, now).Detail; d != "Restarting after a crash" {
		t.Errorf("restart detail: %s", d)
	}
	if p := RuntimeProblem("x"); p.What == "" || p.Next == "" {
		t.Error("runtime problem text")
	}
}
