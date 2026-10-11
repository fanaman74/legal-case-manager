// Package jobstatus reads the background worker's queue counts for the
// Control Center. The worker writes them to a small JSON file in the data
// folder every few seconds; it holds counts only, never case or file names.
package jobstatus

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// MaxAge is how old the counts may be before they are shown as out of date.
const MaxAge = 60 * time.Second

// Kind is one kind of work (converting, indexing, reading mailboxes).
type Kind struct {
	Queued  int `json:"queued"`
	Running int `json:"running"`
	Failed  int `json:"failed"`
	Done    int `json:"done"`
	Total   int `json:"total"`
}

// Index describes the search index.
type Index struct {
	Cases    int    `json:"cases"`
	Chunks   int    `json:"chunks"`
	Model    string `json:"model"`
	Problem  string `json:"problem,omitempty"`
	Indexed  int    `json:"indexed"`
	Pending  int    `json:"pending"`
	Failures int    `json:"failures"`
}

// Status is the worker's latest report.
type Status struct {
	Updated time.Time       `json:"updated"`
	Fresh   bool            `json:"fresh"`
	Jobs    map[string]Kind `json:"jobs"`
	Files   map[string]int  `json:"files"`
	Index   *Index          `json:"index,omitempty"`
}

// File is where the worker writes its counts.
func File(dataDir string) string { return filepath.Join(dataDir, ".run", "worker-status.json") }

// Read returns the worker's counts, or nil if it hasn't written any.
func Read(dataDir string, now time.Time) *Status {
	b, err := os.ReadFile(File(dataDir))
	if err != nil || len(b) > 1<<20 {
		return nil
	}
	var raw struct {
		Updated int64           `json:"updated"`
		Jobs    map[string]Kind `json:"jobs"`
		Files   map[string]int  `json:"files"`
		Index   *Index          `json:"index"`
	}
	if json.Unmarshal(b, &raw) != nil || raw.Updated == 0 {
		return nil
	}
	s := &Status{Updated: time.Unix(raw.Updated, 0).UTC(), Jobs: raw.Jobs, Files: raw.Files, Index: raw.Index}
	s.Fresh = now.Sub(s.Updated) <= MaxAge
	if s.Jobs == nil {
		s.Jobs = map[string]Kind{}
	}
	if s.Files == nil {
		s.Files = map[string]int{}
	}
	return s
}
