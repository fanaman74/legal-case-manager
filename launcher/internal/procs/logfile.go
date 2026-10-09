package procs

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/redact"
)

// Log sizes: each service keeps its current log plus three rotated ones.
const (
	maxLogBytes = 10 << 20
	keepLogs    = 3
)

// LogWriter appends timestamped, redacted lines to a service log and rotates
// it. Secrets and case-file paths are removed before anything reaches disk.
type LogWriter struct {
	path string
	mu   sync.Mutex
	f    *os.File
	size int64
	now  func() time.Time
}

// OpenLog opens (or creates) path for appending.
func OpenLog(path string) (*LogWriter, error) {
	w := &LogWriter{path: path, now: time.Now}
	if err := w.open(); err != nil {
		return nil, err
	}
	return w, nil
}

func (w *LogWriter) open() error {
	f, err := os.OpenFile(w.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o640)
	if err != nil {
		return err
	}
	st, _ := f.Stat()
	w.f, w.size = f, 0
	if st != nil {
		w.size = st.Size()
	}
	return nil
}

func (w *LogWriter) rotate() {
	_ = w.f.Close()
	for i := keepLogs; i >= 1; i-- {
		src := w.path
		if i > 1 {
			src = fmt.Sprintf("%s.%d", w.path, i-1)
		}
		_ = os.Rename(src, fmt.Sprintf("%s.%d", w.path, i))
	}
	if err := w.open(); err != nil {
		w.f = nil
	}
}

// Line writes one line from stream ("stdout", "stderr" or "launcher").
func (w *LogWriter) Line(stream, text string) {
	text = redact.String(strings.TrimRight(text, "\r\n"))
	line := w.now().UTC().Format(time.RFC3339Nano) + " " + stream + " " + text + "\n"
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f == nil {
		return
	}
	if w.size+int64(len(line)) > maxLogBytes {
		w.rotate()
		if w.f == nil {
			return
		}
	}
	n, _ := io.WriteString(w.f, line)
	w.size += int64(n)
}

// Copy reads r line by line into the log until r ends.
func (w *LogWriter) Copy(stream string, r io.Reader) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64<<10), 1<<20)
	for sc.Scan() {
		w.Line(stream, sc.Text())
	}
}

// Close closes the file.
func (w *LogWriter) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f == nil {
		return nil
	}
	err := w.f.Close()
	w.f = nil
	return err
}

// LogLine is one parsed log line.
type LogLine struct {
	Time   string `json:"time"`
	Stream string `json:"stream"`
	Text   string `json:"text"`
}

// Tail returns the last n lines of the service log at path, including the
// most recent rotated file when the current one is short.
func Tail(path string, n int) ([]LogLine, error) {
	lines, err := tailFile(path, n)
	if err != nil {
		return nil, err
	}
	if len(lines) < n {
		older, _ := tailFile(path+".1", n-len(lines))
		lines = append(older, lines...)
	}
	out := make([]LogLine, 0, len(lines))
	for _, l := range lines {
		out = append(out, parseLine(l))
	}
	return out, nil
}

func parseLine(l string) LogLine {
	ts, rest, ok := strings.Cut(l, " ")
	if !ok {
		return LogLine{Text: l}
	}
	if _, err := time.Parse(time.RFC3339Nano, ts); err != nil {
		return LogLine{Text: l}
	}
	stream, text, _ := strings.Cut(rest, " ")
	return LogLine{Time: ts, Stream: stream, Text: text}
}

// tailFile reads at most the last 4 MB of path and returns its last n lines.
func tailFile(path string, n int) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	const window = 4 << 20
	off := st.Size() - window
	if off < 0 {
		off = 0
	}
	buf := make([]byte, st.Size()-off)
	if _, err := f.ReadAt(buf, off); err != nil && err != io.EOF {
		return nil, err
	}
	if off > 0 {
		if i := bytes.IndexByte(buf, '\n'); i >= 0 {
			buf = buf[i+1:]
		}
	}
	all := strings.Split(strings.TrimRight(string(buf), "\n"), "\n")
	if len(all) == 1 && all[0] == "" {
		return nil, nil
	}
	if len(all) > n {
		all = all[len(all)-n:]
	}
	return all, nil
}
