// Package sysinfo samples CPU and memory for a process and everything it
// started (Ollama, for example, runs models in child processes).
package sysinfo

import (
	"runtime"
	"sync"
	"time"
)

// Usage is one service's resource use.
type Usage struct {
	// CPUPercent is the share of the whole computer's CPU (0 to 100).
	CPUPercent float64 `json:"cpuPercent"`
	// MemoryBytes is the memory in use (resident set / working set).
	MemoryBytes uint64 `json:"memoryBytes"`
}

type sample struct {
	cpu time.Duration
	at  time.Time
}

// Sampler remembers the previous CPU reading per process so it can report a
// rate. Safe for concurrent use.
type Sampler struct {
	mu   sync.Mutex
	prev map[int]sample
}

// NewSampler returns an empty sampler.
func NewSampler() *Sampler { return &Sampler{prev: map[int]sample{}} }

// Usage samples pid and its descendants. CPU is reported from the second
// sample onwards; ok is false if the process can't be read.
func (s *Sampler) Usage(pid int) (Usage, bool) {
	if pid <= 0 {
		return Usage{}, false
	}
	cpu, mem, ok := treeTotals(pid)
	if !ok {
		return Usage{}, false
	}
	now := time.Now()
	u := Usage{MemoryBytes: mem}
	s.mu.Lock()
	defer s.mu.Unlock()
	if p, seen := s.prev[pid]; seen && now.After(p.at) && cpu >= p.cpu {
		u.CPUPercent = float64(cpu-p.cpu) / float64(now.Sub(p.at)) / float64(runtime.NumCPU()) * 100
		if u.CPUPercent > 100 {
			u.CPUPercent = 100
		}
	}
	s.prev[pid] = sample{cpu: cpu, at: now}
	return u, true
}

// Forget drops readings for processes that are no longer of interest.
func (s *Sampler) Forget(keep map[int]bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for pid := range s.prev {
		if !keep[pid] {
			delete(s.prev, pid)
		}
	}
}

// descendants returns root and every process whose ancestor chain reaches it.
func descendants(root int, parent map[int]int) []int {
	out := []int{root}
	children := map[int][]int{}
	for pid, pp := range parent {
		if pid != pp {
			children[pp] = append(children[pp], pid)
		}
	}
	seen := map[int]bool{root: true}
	for i := 0; i < len(out); i++ {
		for _, c := range children[out[i]] {
			if !seen[c] {
				seen[c] = true
				out = append(out, c)
			}
		}
	}
	return out
}
