// Package auth holds the launcher's own credentials. The launcher cannot use
// the main app's database because the app may be stopped, so it keeps a small
// file of its own: the Admin's Argon2id hash and, before the Admin exists, the
// hash of a one-time setup code written by the installer.
package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"golang.org/x/crypto/argon2"
)

// Scope is what a session may do.
type Scope string

const (
	// ScopeSetup is granted by the one-time setup code, before an Admin
	// exists. It covers wizard steps 1 to 3 only.
	ScopeSetup Scope = "setup"
	ScopeAdmin Scope = "admin"
)

// Tunables.
const (
	MinPasswordLen = 12
	SessionIdle    = 8 * time.Hour
	MaxFailures    = 5
	LockoutWindow  = 15 * time.Minute
)

// Errors shown to users are written in the server package; these are for
// control flow.
var (
	ErrBadCredentials = errors.New("bad credentials")
	ErrLockedOut      = errors.New("locked out")
	ErrNoSetup        = errors.New("setup already complete")
	ErrWeakPassword   = errors.New("password too short")
	ErrBadUsername    = errors.New("bad username")
)

type fileData struct {
	AdminUser     string    `json:"admin_user,omitempty"`
	AdminHash     string    `json:"admin_hash,omitempty"`
	AdminCreated  time.Time `json:"admin_created,omitempty"`
	SetupCodeHash string    `json:"setup_code_hash,omitempty"`
}

// Session is an authenticated browser session.
type Session struct {
	Token    string
	CSRF     string
	Scope    Scope
	User     string
	LastSeen time.Time
}

// Store manages credentials and sessions.
type Store struct {
	mu       sync.Mutex
	dir      string
	data     fileData
	sessions map[string]*Session
	failures map[string][]time.Time
	now      func() time.Time
}

// Open loads the store from dir. If no Admin exists and no setup code has
// been issued, it issues one, writes it to setup-code.txt and returns it.
func Open(dir string) (*Store, string, error) {
	s := &Store{dir: dir, sessions: map[string]*Session{}, failures: map[string][]time.Time{}, now: time.Now}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, "", err
	}
	b, err := os.ReadFile(s.file())
	switch {
	case err == nil:
		if err := json.Unmarshal(b, &s.data); err != nil {
			return nil, "", fmt.Errorf("read %s: %w", s.file(), err)
		}
	case errors.Is(err, os.ErrNotExist):
	default:
		return nil, "", err
	}
	if s.data.AdminHash != "" || s.data.SetupCodeHash != "" {
		return s, "", nil
	}
	code, err := s.issueSetupCode()
	return s, code, err
}

func (s *Store) file() string { return filepath.Join(s.dir, "auth.json") }

// SetupCodePath is where the plaintext setup code is written.
func (s *Store) SetupCodePath() string { return filepath.Join(s.dir, "setup-code.txt") }

func (s *Store) save() error {
	b, _ := json.MarshalIndent(s.data, "", "  ")
	tmp := s.file() + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, s.file())
}

// Crockford base32 without I, L, O, U: easy to read aloud and type.
const codeAlphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

func newCode() (string, error) {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	var sb strings.Builder
	for i, v := range b {
		if i > 0 && i%4 == 0 {
			sb.WriteByte('-')
		}
		sb.WriteByte(codeAlphabet[int(v)%len(codeAlphabet)])
	}
	return sb.String(), nil
}

func normaliseCode(c string) string {
	c = strings.ToUpper(strings.TrimSpace(c))
	c = strings.NewReplacer("-", "", " ", "", "O", "0", "I", "1", "L", "1").Replace(c)
	return c
}

func codeHash(c string) string {
	sum := sha256.Sum256([]byte(normaliseCode(c)))
	return hex.EncodeToString(sum[:])
}

func (s *Store) issueSetupCode() (string, error) {
	code, err := newCode()
	if err != nil {
		return "", err
	}
	s.data.SetupCodeHash = codeHash(code)
	if err := s.save(); err != nil {
		return "", err
	}
	msg := "Case File Manager setup code\n\n" + code + "\n\nEnter this code in the Control Center to start setup. It stops working once the Admin account exists.\n"
	if err := os.WriteFile(s.SetupCodePath(), []byte(msg), 0o600); err != nil {
		return "", err
	}
	return code, nil
}

// HasAdmin reports whether setup is complete.
func (s *Store) HasAdmin() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.data.AdminHash != ""
}

// AdminUser returns the Admin username, if any.
func (s *Store) AdminUser() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.data.AdminUser
}

func (s *Store) lockedOut(key string) bool {
	cutoff := s.now().Add(-LockoutWindow)
	kept := s.failures[key][:0]
	for _, t := range s.failures[key] {
		if t.After(cutoff) {
			kept = append(kept, t)
		}
	}
	s.failures[key] = kept
	return len(kept) >= MaxFailures
}

func (s *Store) fail(keys ...string) {
	for _, k := range keys {
		s.failures[k] = append(s.failures[k], s.now())
	}
}

// RedeemSetupCode starts a setup session if code matches.
func (s *Store) RedeemSetupCode(code, ip string) (*Session, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.AdminHash != "" || s.data.SetupCodeHash == "" {
		return nil, ErrNoSetup
	}
	key := "setup|" + ip
	if s.lockedOut(key) {
		return nil, ErrLockedOut
	}
	if subtle.ConstantTimeCompare([]byte(codeHash(code)), []byte(s.data.SetupCodeHash)) != 1 {
		s.fail(key)
		return nil, ErrBadCredentials
	}
	return s.newSession(ScopeSetup, "setup")
}

// ValidUsername allows short, plain usernames.
func ValidUsername(u string) bool {
	if l := utf8.RuneCountInString(u); l < 2 || l > 64 {
		return false
	}
	for _, r := range u {
		if !(r == '.' || r == '_' || r == '-' || r == '@' || r >= '0' && r <= '9' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z') {
			return false
		}
	}
	return true
}

// CreateAdmin creates the Admin account from a setup session, retires the
// setup code, and upgrades the session to Admin.
func (s *Store) CreateAdmin(sess *Session, user, password string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.AdminHash != "" || sess.Scope != ScopeSetup {
		return ErrNoSetup
	}
	if !ValidUsername(user) {
		return ErrBadUsername
	}
	if utf8.RuneCountInString(password) < MinPasswordLen {
		return ErrWeakPassword
	}
	h, err := hashPassword(password)
	if err != nil {
		return err
	}
	s.data.AdminUser, s.data.AdminHash, s.data.AdminCreated = user, h, s.now().UTC()
	s.data.SetupCodeHash = ""
	if err := s.save(); err != nil {
		return err
	}
	_ = os.Remove(s.SetupCodePath())
	// Every other setup session ends now.
	for tok, other := range s.sessions {
		if other.Scope == ScopeSetup && other != sess {
			delete(s.sessions, tok)
		}
	}
	sess.Scope, sess.User = ScopeAdmin, user
	return nil
}

// Login checks Admin credentials.
func (s *Store) Login(user, password, ip string) (*Session, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	ipKey, userKey := "login|"+ip, "user|"+strings.ToLower(user)
	if s.lockedOut(ipKey) || s.lockedOut(userKey) {
		return nil, ErrLockedOut
	}
	ok := s.data.AdminHash != "" && strings.EqualFold(user, s.data.AdminUser) && verifyPassword(s.data.AdminHash, password)
	if !ok {
		if s.data.AdminHash == "" {
			// Burn similar time so timing doesn't reveal setup state.
			_, _ = hashPassword(password)
		}
		s.fail(ipKey, userKey)
		return nil, ErrBadCredentials
	}
	delete(s.failures, ipKey)
	delete(s.failures, userKey)
	return s.newSession(ScopeAdmin, s.data.AdminUser)
}

func randomToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func (s *Store) newSession(scope Scope, user string) (*Session, error) {
	tok, err := randomToken()
	if err != nil {
		return nil, err
	}
	csrf, err := randomToken()
	if err != nil {
		return nil, err
	}
	sess := &Session{Token: tok, CSRF: csrf, Scope: scope, User: user, LastSeen: s.now()}
	s.sessions[tok] = sess
	return sess, nil
}

// Lookup returns a live session and refreshes its idle timer.
func (s *Store) Lookup(token string) (*Session, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[token]
	if !ok {
		return nil, false
	}
	if s.now().Sub(sess.LastSeen) > SessionIdle {
		delete(s.sessions, token)
		return nil, false
	}
	// A setup session outlives setup only by being upgraded in CreateAdmin.
	if sess.Scope == ScopeSetup && s.data.AdminHash != "" {
		delete(s.sessions, token)
		return nil, false
	}
	sess.LastSeen = s.now()
	return sess, true
}

// Logout ends a session.
func (s *Store) Logout(token string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.sessions, token)
}

// Argon2id parameters (OWASP baseline).
const (
	argonTime    = 3
	argonMemory  = 64 * 1024
	argonThreads = 2
	argonKeyLen  = 32
)

func hashPassword(pw string) (string, error) {
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key := argon2.IDKey([]byte(pw), salt, argonTime, argonMemory, argonThreads, argonKeyLen)
	return fmt.Sprintf("$argon2id$v=19$m=%d,t=%d,p=%d$%s$%s", argonMemory, argonTime, argonThreads,
		base64.RawStdEncoding.EncodeToString(salt), base64.RawStdEncoding.EncodeToString(key)), nil
}

func verifyPassword(encoded, pw string) bool {
	parts := strings.Split(encoded, "$")
	if len(parts) != 6 || parts[1] != "argon2id" {
		return false
	}
	var m, t uint32
	var p uint8
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &m, &t, &p); err != nil {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil {
		return false
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil {
		return false
	}
	got := argon2.IDKey([]byte(pw), salt, t, m, p, uint32(len(want)))
	return subtle.ConstantTimeCompare(got, want) == 1
}
