package config

import (
	"os"
	"path/filepath"
	"testing"
)

// Windows PowerShell 5.1 writes launcher.json with a UTF-8 byte-order mark.
func TestLoadAcceptsByteOrderMark(t *testing.T) {
	path := filepath.Join(t.TempDir(), "launcher.json")
	if err := os.WriteFile(path, []byte("\xef\xbb\xbf{\"app_port\": 9443}"), 0o600); err != nil {
		t.Fatal(err)
	}
	c, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if c.AppPort != 9443 {
		t.Fatalf("AppPort = %d, want 9443", c.AppPort)
	}
}
