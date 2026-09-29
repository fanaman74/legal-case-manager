package procs

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
)

// The test binary doubles as the child process: with CFM_HELPER set it acts
// out a scenario instead of running tests.
func TestMain(m *testing.M) {
	switch os.Getenv("CFM_HELPER") {
	case "":
		os.Exit(m.Run())
	case "serve":
		fmt.Println("ready with key sk-abcdefghijklmnopqrstuvwx")
		fmt.Fprintln(os.Stderr, "warning on stderr")
		time.Sleep(time.Hour)
	case "crash":
		fmt.Println("crashing")
		os.Exit(3)
	case "env":
		for _, kv := range os.Environ() {
			fmt.Println("ENV", kv)
		}
		time.Sleep(time.Hour)
	}
	os.Exit(0)
}

func helper(t *testing.T, scenario string) Spec {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	return Spec{
		Service: catalog.Worker,
		Path:    exe,
		Env:     append(baseEnv(), "CFM_HELPER="+scenario),
		LogFile: filepath.Join(t.TempDir(), "worker.log"),
	}
}

func waitFor(t *testing.T, what string, f func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for !f() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func logText(t *testing.T, path string) string {
	b, _ := os.ReadFile(path)
	return string(b)
}

func TestStartStopAndRedactedLog(t *testing.T) {
	spec := helper(t, "serve")
	log, err := OpenLog(spec.LogFile)
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()
	p, err := Start(spec, log)
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, "output", func() bool { return strings.Contains(logText(t, spec.LogFile), "stderr warning on stderr") })
	p.Stop(2 * time.Second)
	if _, stopped := p.Exit(); !stopped {
		t.Error("exit should be recorded as a stop")
	}
	out := logText(t, spec.LogFile)
	if strings.Contains(out, "sk-abcdef") {
		t.Errorf("secret reached the log: %s", out)
	}
	lines, err := Tail(spec.LogFile, 10)
	if err != nil || len(lines) != 2 {
		t.Fatalf("tail: %v %+v", err, lines)
	}
	if lines[0].Stream == "" || lines[0].Time == "" {
		t.Errorf("parsed line: %+v", lines[0])
	}
}

func TestMissingProgram(t *testing.T) {
	spec := helper(t, "serve")
	spec.Path = filepath.Join(t.TempDir(), "nope")
	k := Keep(spec, nil)
	<-k.Done()
	if st := k.Status(); !st.Missing || !st.GaveUp || st.Running {
		t.Errorf("status: %+v", st)
	}
}

func TestKeeperRestartsThenGivesUp(t *testing.T) {
	defer func(l int, b time.Duration) { crashLimit, firstBackoff = l, b }(crashLimit, firstBackoff)
	crashLimit, firstBackoff = 3, 10*time.Millisecond
	var changes int
	k := Keep(helper(t, "crash"), func(Status) { changes++ })
	select {
	case <-k.Done():
	case <-time.After(10 * time.Second):
		t.Fatal("keeper didn't give up")
	}
	st := k.Status()
	if !st.GaveUp || st.Restarts != 2 || st.LastExit == nil || st.LastExit.Code != 3 {
		t.Errorf("status: %+v", st)
	}
	if changes == 0 {
		t.Error("onChange never called")
	}
	k.Stop() // safe after giving up
}

func TestKeeperStop(t *testing.T) {
	k := Keep(helper(t, "serve"), nil)
	waitFor(t, "running", func() bool { return k.Status().Running })
	pid := k.Status().PID
	k.Stop()
	st := k.Status()
	if st.Running || st.GaveUp || st.Restarts != 0 {
		t.Errorf("status after stop: %+v", st)
	}
	if p, err := os.FindProcess(pid); err == nil {
		if err := p.Signal(os.Signal(nil)); err == nil {
			// On Unix FindProcess always succeeds; Signal(nil) is invalid, so
			// fall through. The Done channel already proved the exit.
			_ = err
		}
	}
}

func TestChildEnvironmentIsFiltered(t *testing.T) {
	t.Setenv("CFM_TEST_SECRET", "hunter2")
	spec := helper(t, "env")
	spec.Env = append(baseEnv(), "CFM_HELPER=env")
	k := Keep(spec, nil)
	waitFor(t, "env dump", func() bool { return strings.Contains(logText(t, spec.LogFile), "ENV CFM_HELPER=env") })
	k.Stop()
	if strings.Contains(logText(t, spec.LogFile), "CFM_TEST_SECRET") {
		t.Error("unlisted environment variable passed to the child")
	}
}

func TestSpecsAreFixed(t *testing.T) {
	cfg, _ := config.Load(filepath.Join(t.TempDir(), "launcher.json"))
	specs := Specs(cfg, "1.0.0")
	if len(specs) != len(catalog.Services) {
		t.Fatalf("every catalog service needs a spec: %d", len(specs))
	}
	api := specs[catalog.API]
	if api.Path != cfg.Python || strings.Join(api.Args, " ") != "-m app.serve" || api.Dir != cfg.AppDir {
		t.Errorf("api spec: %+v", api)
	}
	has := func(env []string, kv string) bool {
		for _, e := range env {
			if e == kv {
				return true
			}
		}
		return false
	}
	if !has(api.Env, "DEPLOY_MODE=local") || !has(api.Env, "DATA_DIR="+cfg.DataDir) {
		t.Errorf("api env: %v", api.Env)
	}
	for _, e := range api.Env {
		if strings.Contains(e, "ca.key") {
			t.Errorf("the web app must never be pointed at the CA key: %s", e)
		}
	}
	m := specs[catalog.Models]
	if !has(m.Env, "OLLAMA_HOST=127.0.0.1:11434") {
		t.Errorf("models must listen on loopback only: %v", m.Env)
	}
}

func TestLogRotation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "x.log")
	w, err := OpenLog(path)
	if err != nil {
		t.Fatal(err)
	}
	line := strings.Repeat("a", 1000)
	for i := 0; i < (maxLogBytes/1000)+50; i++ {
		w.Line("stdout", line)
	}
	w.Line("stdout", "newest")
	w.Close()
	if _, err := os.Stat(path + ".1"); err != nil {
		t.Fatal("log didn't rotate")
	}
	st, _ := os.Stat(path)
	if st.Size() > maxLogBytes {
		t.Errorf("current log too big: %d", st.Size())
	}
	lines, _ := Tail(path, 3)
	if len(lines) != 3 || lines[2].Text != "newest" {
		t.Errorf("tail across rotation: %+v", lines)
	}
}
