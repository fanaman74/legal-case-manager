package procs

import (
	"errors"
	"os"
	"os/exec"
	"sync"
	"time"
)

// Proc is one running child process.
type Proc struct {
	PID     int
	Started time.Time

	cmd  *exec.Cmd
	plat platform
	done chan struct{}

	mu       sync.Mutex
	exitCode int
	stopping bool
}

// ErrMissing means the program isn't installed.
var ErrMissing = errors.New("program not installed")

// Start launches spec with its output copied into log. The child is placed
// so that it (and anything it starts) is killed if this process dies.
func Start(spec Spec, log *LogWriter) (*Proc, error) {
	if _, err := os.Stat(spec.Path); err != nil {
		return nil, ErrMissing
	}
	cmd := exec.Command(spec.Path, spec.Args...)
	cmd.Dir = spec.Dir
	cmd.Env = spec.Env
	cmd.Stdin = nil
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	p := &Proc{cmd: cmd, done: make(chan struct{})}
	p.plat.prepare(cmd)
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	p.PID, p.Started = cmd.Process.Pid, time.Now()
	if err := p.plat.attach(cmd); err != nil {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		return nil, err
	}
	var copies sync.WaitGroup
	copies.Add(2)
	go func() { defer copies.Done(); log.Copy("stdout", stdout) }()
	go func() { defer copies.Done(); log.Copy("stderr", stderr) }()
	go func() {
		copies.Wait()
		err := cmd.Wait()
		code := 0
		if err != nil {
			code = -1
			var ee *exec.ExitError
			if errors.As(err, &ee) {
				code = ee.ExitCode()
			}
		}
		p.mu.Lock()
		p.exitCode = code
		p.mu.Unlock()
		p.plat.release()
		close(p.done)
	}()
	return p, nil
}

// Done is closed when the process has exited.
func (p *Proc) Done() <-chan struct{} { return p.done }

// Exit returns the exit code (valid after Done) and whether Stop caused it.
func (p *Proc) Exit() (code int, stopped bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.exitCode, p.stopping
}

// Stop asks the process to exit, then kills it and its children after grace.
func (p *Proc) Stop(grace time.Duration) {
	p.mu.Lock()
	p.stopping = true
	p.mu.Unlock()
	select {
	case <-p.done:
		return
	default:
	}
	p.plat.terminate(p.cmd)
	select {
	case <-p.done:
		return
	case <-time.After(grace):
	}
	p.plat.kill(p.cmd)
	<-p.done
}
