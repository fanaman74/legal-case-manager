package sysinfo

import (
	"os"
	"runtime"
	"testing"
	"time"
)

func TestDescendants(t *testing.T) {
	parent := map[int]int{1: 0, 10: 1, 11: 10, 12: 10, 13: 11, 20: 1, 99: 99}
	got := descendants(10, parent)
	want := map[int]bool{10: true, 11: true, 12: true, 13: true}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for _, p := range got {
		if !want[p] {
			t.Errorf("unexpected %d", p)
		}
	}
}

func TestSelfUsage(t *testing.T) {
	if runtime.GOOS != "linux" && runtime.GOOS != "windows" {
		t.Skip("no sampler on this OS")
	}
	s := NewSampler()
	u, ok := s.Usage(os.Getpid())
	if !ok || u.MemoryBytes == 0 {
		t.Fatalf("first sample: %+v %v", u, ok)
	}
	end := time.Now().Add(150 * time.Millisecond)
	for time.Now().Before(end) {
	}
	u, ok = s.Usage(os.Getpid())
	if !ok || u.CPUPercent <= 0 || u.CPUPercent > 100 {
		t.Errorf("second sample: %+v", u)
	}
	if _, ok := s.Usage(0); ok {
		t.Error("pid 0 must not be sampled")
	}
}
