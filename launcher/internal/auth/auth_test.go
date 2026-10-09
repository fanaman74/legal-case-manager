package auth

import (
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

func open(t *testing.T) (*Store, string, string) {
	t.Helper()
	dir := t.TempDir()
	s, code, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	return s, code, dir
}

func TestSetupCodeIssuedOnceAndWrittenToFile(t *testing.T) {
	s, code, dir := open(t)
	if code == "" {
		t.Fatal("expected a setup code on first open")
	}
	b, err := os.ReadFile(s.SetupCodePath())
	if err != nil || !strings.Contains(string(b), code) {
		t.Fatalf("setup-code.txt missing code: %v", err)
	}
	auth, _ := os.ReadFile(dir + "/auth.json")
	if strings.Contains(string(auth), code) {
		t.Fatal("auth.json must hold only the hash of the code")
	}
	_, again, err := Open(dir)
	if err != nil || again != "" {
		t.Fatalf("second open should not issue a new code (got %q, %v)", again, err)
	}
}

func TestSetupFlow(t *testing.T) {
	s, code, _ := open(t)
	if _, err := s.RedeemSetupCode("WRONG-CODE-0000", "127.0.0.1"); !errors.Is(err, ErrBadCredentials) {
		t.Fatalf("wrong code: %v", err)
	}
	// Codes are accepted without dashes and in lower case.
	sess, err := s.RedeemSetupCode(strings.ToLower(strings.ReplaceAll(code, "-", "")), "127.0.0.1")
	if err != nil {
		t.Fatal(err)
	}
	if sess.Scope != ScopeSetup {
		t.Fatalf("scope %s", sess.Scope)
	}
	other, _ := s.RedeemSetupCode(code, "127.0.0.1")

	if err := s.CreateAdmin(sess, "fred", "short"); !errors.Is(err, ErrWeakPassword) {
		t.Fatalf("weak password: %v", err)
	}
	if err := s.CreateAdmin(sess, "fred smith", "a long enough password"); !errors.Is(err, ErrBadUsername) {
		t.Fatalf("bad username: %v", err)
	}
	if err := s.CreateAdmin(sess, "fred", "a long enough password"); err != nil {
		t.Fatal(err)
	}
	if sess.Scope != ScopeAdmin || sess.User != "fred" {
		t.Fatalf("session not upgraded: %+v", sess)
	}
	if _, err := os.Stat(s.SetupCodePath()); !os.IsNotExist(err) {
		t.Fatal("setup-code.txt should be deleted once the Admin exists")
	}
	if _, ok := s.Lookup(other.Token); ok {
		t.Fatal("other setup sessions must end when the Admin is created")
	}
	if _, err := s.RedeemSetupCode(code, "127.0.0.1"); !errors.Is(err, ErrNoSetup) {
		t.Fatalf("setup code must stop working: %v", err)
	}
	if err := s.CreateAdmin(sess, "mallory", "another long password"); !errors.Is(err, ErrNoSetup) {
		t.Fatalf("second admin: %v", err)
	}
}

func adminStore(t *testing.T) *Store {
	t.Helper()
	s, code, _ := open(t)
	sess, err := s.RedeemSetupCode(code, "127.0.0.1")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.CreateAdmin(sess, "fred", "correct horse battery"); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestLoginAndLockout(t *testing.T) {
	s := adminStore(t)
	clock := time.Now()
	s.now = func() time.Time { return clock }

	if _, err := s.Login("FRED", "correct horse battery", "10.0.0.2"); err != nil {
		t.Fatalf("username should be case-insensitive: %v", err)
	}
	for i := 0; i < MaxFailures; i++ {
		if _, err := s.Login("fred", "wrong", "10.0.0.2"); !errors.Is(err, ErrBadCredentials) {
			t.Fatalf("attempt %d: %v", i, err)
		}
	}
	if _, err := s.Login("fred", "correct horse battery", "10.0.0.2"); !errors.Is(err, ErrLockedOut) {
		t.Fatalf("expected lockout, got %v", err)
	}
	// The account is locked from other addresses too.
	if _, err := s.Login("fred", "correct horse battery", "10.0.0.3"); !errors.Is(err, ErrLockedOut) {
		t.Fatalf("expected account lockout, got %v", err)
	}
	clock = clock.Add(LockoutWindow + time.Second)
	if _, err := s.Login("fred", "correct horse battery", "10.0.0.2"); err != nil {
		t.Fatalf("lockout should expire: %v", err)
	}
}

func TestSessionIdleTimeoutAndLogout(t *testing.T) {
	s := adminStore(t)
	clock := time.Now()
	s.now = func() time.Time { return clock }
	sess, err := s.Login("fred", "correct horse battery", "127.0.0.1")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := s.Lookup(sess.Token); !ok {
		t.Fatal("fresh session should be valid")
	}
	clock = clock.Add(SessionIdle + time.Minute)
	if _, ok := s.Lookup(sess.Token); ok {
		t.Fatal("idle session should expire")
	}
	sess, _ = s.Login("fred", "correct horse battery", "127.0.0.1")
	s.Logout(sess.Token)
	if _, ok := s.Lookup(sess.Token); ok {
		t.Fatal("logged-out session should be invalid")
	}
}

func TestPasswordHashRoundTrip(t *testing.T) {
	h, err := hashPassword("pässwörd with unicode")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(h, "$argon2id$v=19$") {
		t.Fatalf("unexpected encoding %s", h)
	}
	if !verifyPassword(h, "pässwörd with unicode") || verifyPassword(h, "pässwörd with unicodE") {
		t.Fatal("verify mismatch")
	}
	if verifyPassword("garbage", "x") {
		t.Fatal("garbage hash must not verify")
	}
}
