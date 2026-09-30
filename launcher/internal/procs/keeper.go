package procs

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

// Crash policy: restart with a growing delay, and give up if the service
// crashes crashLimit times within crashWindow (a restart loop helps no one
// and fills the log).
// They are variables so tests can shorten them.
var (
	crashLimit   = 5
	crashWindow  = 5 * time.Minute
	firstBackoff = 2 * time.Second
	maxBackoff   = 30 * time.Second
	stopGrace    = 10 * time.Second
)

// Exit records how the process last ended.
type Exit struct {
	Code int       `json:"code"`
	At   time.Time `json:"at"`
}

// Status is what the launcher needs to know about a kept process.
type Status struct {
	Service   catalog.ServiceID `json:"service"`
	Running   bool              `json:"running"`
	PID       int               `json:"pid,omitempty"`
	StartedAt *time.Time        `json:"startedAt,omitempty"`
	// Restarts counts automatic restarts after crashes since the Admin
	// last started the service.
	Restarts      int        `json:"restarts"`
	LastRestartAt *time.Time `json:"lastRestartAt,omitempty"`
	LastExit      *Exit      `json:"lastExit,omitempty"`
	// GaveUp is set when the service crashed too often and was left stopped.
	GaveUp bool `json:"gaveUp,omitempty"`
	// Missing is set when the program isn't installed.
	Missing   bool      `json:"missing,omitempty"`
	StartErr  string    `json:"startError,omitempty"`
	UpdatedAt time.Time `json:"updatedAt"`
}

// Keeper runs one service and restarts it after a crash.
type Keeper struct {
	spec     Spec
	onChange func(Status)

	mu     sync.Mutex
	status Status
	proc   *Proc
	stop   chan struct{}
	done   chan struct{}
}

// Keep starts spec and keeps it running until Stop. onChange, if set, is
// called after every status change.
func Keep(spec Spec, onChange func(Status)) *Keeper {
	k := &Keeper{
		spec:     spec,
		onChange: onChange,
		status:   Status{Service: spec.Service, UpdatedAt: time.Now()},
		stop:     make(chan struct{}),
		done:     make(chan struct{}),
	}
	go k.loop()
	return k
}

// Status returns a copy of the current status.
func (k *Keeper) Status() Status {
	k.mu.Lock()
	defer k.mu.Unlock()
	return k.status
}

// Done is closed when the keeper has stopped for good (Stop, or gave up).
func (k *Keeper) Done() <-chan struct{} { return k.done }

// Stop stops the process and waits for it to exit.
func (k *Keeper) Stop() {
	k.mu.Lock()
	select {
	case <-k.stop:
	default:
		close(k.stop)
	}
	p := k.proc
	k.mu.Unlock()
	if p != nil {
		p.Stop(stopGrace)
	}
	<-k.done
}

func (k *Keeper) update(f func(*Status)) {
	k.mu.Lock()
	f(&k.status)
	k.status.UpdatedAt = time.Now()
	st := k.status
	k.mu.Unlock()
	if k.onChange != nil {
		k.onChange(st)
	}
}

func (k *Keeper) stopped() bool {
	select {
	case <-k.stop:
		return true
	default:
		return false
	}
}

func (k *Keeper) loop() {
	defer close(k.done)
	if err := os.MkdirAll(filepath.Dir(k.spec.LogFile), 0o750); err != nil {
		k.update(func(s *Status) { s.StartErr = err.Error(); s.GaveUp = true })
		return
	}
	log, err := OpenLog(k.spec.LogFile)
	if err != nil {
		k.update(func(s *Status) { s.StartErr = err.Error(); s.GaveUp = true })
		return
	}
	defer log.Close()

	var crashes []time.Time
	backoff := firstBackoff
	for !k.stopped() {
		k.mu.Lock()
		if k.stopped() {
			k.mu.Unlock()
			return
		}
		p, err := Start(k.spec, log)
		if err == nil {
			k.proc = p
		}
		k.mu.Unlock()
		if err != nil {
			missing := errors.Is(err, ErrMissing)
			log.Line("launcher", "couldn't start: "+err.Error())
			k.update(func(s *Status) {
				s.Running, s.PID, s.StartedAt = false, 0, nil
				s.Missing, s.StartErr, s.GaveUp = missing, err.Error(), true
			})
			return
		}
		started := p.Started
		k.update(func(s *Status) {
			s.Running, s.PID, s.StartedAt = true, p.PID, &started
			s.Missing, s.StartErr, s.GaveUp = false, "", false
		})
		log.Line("launcher", fmt.Sprintf("started (pid %d)", p.PID))

		<-p.Done()
		code, byStop := p.Exit()
		now := time.Now()
		k.mu.Lock()
		k.proc = nil
		k.mu.Unlock()
		k.update(func(s *Status) {
			s.Running, s.PID, s.StartedAt = false, 0, nil
			s.LastExit = &Exit{Code: code, At: now}
		})
		if byStop || k.stopped() {
			log.Line("launcher", "stopped")
			return
		}

		// Crashed (or exited by itself, which a service never should).
		if now.Sub(started) > crashWindow {
			backoff = firstBackoff
		}
		recent := crashes[:0]
		for _, t := range crashes {
			if now.Sub(t) < crashWindow {
				recent = append(recent, t)
			}
		}
		crashes = append(recent, now)
		if len(crashes) >= crashLimit {
			log.Line("launcher", fmt.Sprintf("exited with code %d; crashed %d times in %s, not restarting", code, len(crashes), crashWindow))
			k.update(func(s *Status) { s.GaveUp = true })
			return
		}
		log.Line("launcher", fmt.Sprintf("exited with code %d; restarting in %s", code, backoff))
		select {
		case <-k.stop:
			return
		case <-time.After(backoff):
		}
		backoff *= 2
		if backoff > maxBackoff {
			backoff = maxBackoff
		}
		restartAt := time.Now()
		k.update(func(s *Status) { s.Restarts++; s.LastRestartAt = &restartAt })
	}
}

// WriteStatus saves st to path atomically (used by the Windows service host).
func WriteStatus(path string, st Status) error {
	b, _ := json.Marshal(st)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o640); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// ReadStatus reads a status file written by WriteStatus.
func ReadStatus(path string) (Status, error) {
	var st Status
	b, err := os.ReadFile(path)
	if err != nil {
		return st, err
	}
	err = json.Unmarshal(b, &st)
	return st, err
}
