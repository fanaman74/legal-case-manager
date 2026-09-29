package tlsca

import (
	"net"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestIssueAndRead(t *testing.T) {
	dir := t.TempDir()
	if err := EnsureCA(dir); err != nil {
		t.Fatal(err)
	}
	caBefore, _ := os.ReadFile(filepath.Join(dir, CACert))
	lan := net.ParseIP("192.168.1.20")
	if err := IssueServer(dir, []string{"localhost", "casefiles.local"}, []net.IP{net.ParseIP("127.0.0.1"), lan}); err != nil {
		t.Fatal(err)
	}
	info, err := ReadServer(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !info.Covers(lan) || info.Covers(net.ParseIP("192.168.1.21")) {
		t.Fatalf("covers: %+v", info.IPs)
	}
	if d := time.Until(info.NotAfter); d > 398*24*time.Hour || d < 390*24*time.Hour {
		t.Fatalf("leaf validity %v must stay under the browser limit", d)
	}
	// Renewing the leaf keeps the CA, so devices that trust it keep working.
	if err := EnsureCA(dir); err != nil {
		t.Fatal(err)
	}
	if err := IssueServer(dir, []string{"localhost"}, nil); err != nil {
		t.Fatal(err)
	}
	caAfter, _ := os.ReadFile(filepath.Join(dir, CACert))
	if string(caBefore) != string(caAfter) {
		t.Fatal("CA must not change on renewal")
	}
	if runtime.GOOS != "windows" {
		st, _ := os.Stat(filepath.Join(dir, CAKey))
		if st.Mode().Perm() != 0o600 {
			t.Fatalf("CA key mode %v", st.Mode().Perm())
		}
	}
}
