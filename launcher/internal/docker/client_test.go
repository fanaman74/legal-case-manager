package docker

import (
	"encoding/binary"
	"testing"
)

func frame(stream byte, s string) []byte {
	h := make([]byte, 8)
	h[0] = stream
	binary.BigEndian.PutUint32(h[4:], uint32(len(s)))
	return append(h, s...)
}

func TestDemux(t *testing.T) {
	b := append(frame(1, "2026-09-29T10:00:00Z started\n"), frame(2, "2026-09-29T10:00:01Z boom\n2026-09-29T10:00:02Z again\n")...)
	lines := demux(b)
	if len(lines) != 3 {
		t.Fatalf("got %d lines: %+v", len(lines), lines)
	}
	if lines[0].Stream != "stdout" || lines[0].Text != "started" || lines[0].Time != "2026-09-29T10:00:00Z" {
		t.Errorf("line 0: %+v", lines[0])
	}
	if lines[1].Stream != "stderr" || lines[2].Text != "again" {
		t.Errorf("stderr lines: %+v", lines[1:])
	}
	// TTY containers send plain text.
	if raw := demux([]byte("2026-09-29T10:00:00Z plain\n")); len(raw) != 1 || raw[0].Text != "plain" {
		t.Errorf("raw: %+v", raw)
	}
}

func TestUsage(t *testing.T) {
	var s statsWire
	s.CPUStats.CPUUsage.TotalUsage = 2_000_000
	s.PreCPUStats.CPUUsage.TotalUsage = 1_000_000
	s.CPUStats.SystemUsage = 20_000_000
	s.PreCPUStats.SystemUsage = 10_000_000
	s.CPUStats.OnlineCPUs = 4
	s.MemoryStats.Usage = 300
	s.MemoryStats.Stats = map[string]uint64{"inactive_file": 100}
	u := usageFrom(s)
	if u.CPUPercent != 40 {
		t.Errorf("cpu %.1f want 40", u.CPUPercent)
	}
	if u.MemoryBytes != 200 {
		t.Errorf("mem %d want 200", u.MemoryBytes)
	}
}
