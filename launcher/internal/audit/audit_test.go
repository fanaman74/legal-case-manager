package audit

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestChainAndReopen(t *testing.T) {
	p := filepath.Join(t.TempDir(), "audit.jsonl")
	l, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	for _, a := range []string{"one", "two", "three"} {
		if err := l.Append(Entry{Actor: "fred", Action: a, Outcome: Succeeded}); err != nil {
			t.Fatal(err)
		}
	}
	// Reopening continues the same chain.
	l2, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	if err := l2.Append(Entry{Actor: "fred", Action: "four", Outcome: Succeeded}); err != nil {
		t.Fatal(err)
	}
	n, err := Verify(p)
	if err != nil || n != 4 {
		t.Fatalf("verify: n=%d err=%v", n, err)
	}
	recent, _ := l2.Recent(2)
	if len(recent) != 2 || recent[0].Action != "four" || recent[1].Action != "three" {
		t.Fatalf("recent: %+v", recent)
	}
}

func TestTamperDetected(t *testing.T) {
	for name, mutate := range map[string]func([]string) []string{
		"edited":    func(l []string) []string { l[1] = strings.Replace(l[1], `"fred"`, `"eve"`, 1); return l },
		"deleted":   func(l []string) []string { return append(l[:1], l[2:]...) },
		"reordered": func(l []string) []string { l[0], l[1] = l[1], l[0]; return l },
	} {
		t.Run(name, func(t *testing.T) {
			p := filepath.Join(t.TempDir(), "audit.jsonl")
			l, _ := Open(p)
			for i := 0; i < 3; i++ {
				_ = l.Append(Entry{Actor: "fred", Action: "x", Outcome: Succeeded})
			}
			b, _ := os.ReadFile(p)
			lines := strings.Split(strings.TrimSpace(string(b)), "\n")
			lines = mutate(lines)
			_ = os.WriteFile(p, []byte(strings.Join(lines, "\n")+"\n"), 0o600)
			_, err := Verify(p)
			var te ErrTampered
			if !errors.As(err, &te) {
				t.Fatalf("expected tamper error, got %v", err)
			}
		})
	}
}

func TestAppendFailsWhenLogUnwritable(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("permission bits not enforced")
	}
	dir := t.TempDir()
	p := filepath.Join(dir, "audit.jsonl")
	l, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	_ = os.Chmod(p, 0o400)
	if err := l.Append(Entry{Actor: "fred", Action: "x"}); err == nil {
		t.Fatal("Append must report failure so the action is refused")
	}
}
