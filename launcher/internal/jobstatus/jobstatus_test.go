package jobstatus

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

func TestRead(t *testing.T) {
	dir := t.TempDir()
	now := time.Now()
	if Read(dir, now) != nil {
		t.Fatal("no file should read as nil")
	}
	if err := os.MkdirAll(filepath.Join(dir, ".run"), 0o755); err != nil {
		t.Fatal(err)
	}
	body := `{"updated": ` + strconv.FormatInt(now.Unix()-5, 10) + `, "jobs": {"convert": {"queued": 3, "running": 1, "failed": 2, "done": 0, "total": 0}}, "files": {"converted": 10}, "index": {"cases": 2, "chunks": 400, "model": "bge-m3"}}`
	if err := os.WriteFile(File(dir), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	s := Read(dir, now)
	if s == nil || !s.Fresh || s.Jobs["convert"].Queued != 3 || s.Jobs["convert"].Failed != 2 || s.Files["converted"] != 10 || s.Index.Chunks != 400 {
		t.Fatalf("unexpected status %+v", s)
	}
	if Read(dir, now.Add(2*time.Minute)).Fresh {
		t.Fatal("old counts should not be fresh")
	}
	if err := os.WriteFile(File(dir), []byte("not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if Read(dir, now) != nil {
		t.Fatal("bad json should read as nil")
	}
}
