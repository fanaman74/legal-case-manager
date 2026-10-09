// Package audit writes an append-only JSON Lines log in which every entry
// carries the hash of the previous one, so edits or deletions are detectable.
package audit

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"time"
)

// Entry is one audit record.
type Entry struct {
	Time    time.Time `json:"time"`
	Actor   string    `json:"actor"`
	IP      string    `json:"ip"`
	Action  string    `json:"action"`
	Target  string    `json:"target,omitempty"`
	Outcome string    `json:"outcome"`
	Detail  string    `json:"detail,omitempty"`
	Prev    string    `json:"prev"`
	Hash    string    `json:"hash"`
}

// Outcomes.
const (
	Requested = "requested"
	Succeeded = "succeeded"
	Failed    = "failed"
	Denied    = "denied"
)

// Log is safe for concurrent use.
type Log struct {
	mu   sync.Mutex
	path string
	last string
	now  func() time.Time
}

// Open opens (or creates) the log and recovers the last hash. It does not
// verify the chain; call Verify for that.
func Open(path string) (*Log, error) {
	l := &Log{path: path, now: time.Now}
	f, err := os.OpenFile(path, os.O_RDONLY|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 1<<20)
	for sc.Scan() {
		var e Entry
		if json.Unmarshal(sc.Bytes(), &e) == nil {
			l.last = e.Hash
		}
	}
	return l, sc.Err()
}

func hashOf(e Entry) string {
	e.Hash = ""
	b, _ := json.Marshal(e)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// Append writes e and flushes it to disk before returning. Callers must not
// perform the audited action if Append fails.
func (l *Log) Append(e Entry) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if e.Time.IsZero() {
		e.Time = l.now().UTC()
	}
	e.Prev = l.last
	e.Hash = hashOf(e)
	line, err := json.Marshal(e)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(l.path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err != nil {
		return err
	}
	if _, err := f.Write(append(line, '\n')); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	l.last = e.Hash
	return nil
}

// Recent returns up to n of the newest entries, newest first.
func (l *Log) Recent(n int) ([]Entry, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	b, err := os.ReadFile(l.path)
	if err != nil {
		return nil, err
	}
	lines := bytes.Split(bytes.TrimSpace(b), []byte("\n"))
	var out []Entry
	for i := len(lines) - 1; i >= 0 && len(out) < n; i-- {
		var e Entry
		if json.Unmarshal(lines[i], &e) == nil {
			out = append(out, e)
		}
	}
	return out, nil
}

// ErrTampered reports the first line where the chain breaks.
type ErrTampered struct{ Line int }

func (e ErrTampered) Error() string { return fmt.Sprintf("audit chain broken at line %d", e.Line) }

// Verify checks the whole chain and returns the number of entries.
func Verify(path string) (int, error) {
	f, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 1<<20)
	prev, n := "", 0
	for sc.Scan() {
		n++
		var e Entry
		if err := json.Unmarshal(sc.Bytes(), &e); err != nil {
			return n, ErrTampered{n}
		}
		if e.Prev != prev || hashOf(e) != e.Hash {
			return n, ErrTampered{n}
		}
		prev = e.Hash
	}
	return n, sc.Err()
}
