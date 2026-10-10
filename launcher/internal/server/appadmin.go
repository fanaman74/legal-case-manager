package server

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// appAdminFile is where the launcher hands the Admin account to the main app.
// The app signs the Admin in with this hash, so there is one Admin password,
// set in the setup wizard. The file is in the data folder, which the app can
// already write, so it gives the app nothing it couldn't do itself; nothing
// flows back from the app to the launcher.
func (s *Server) appAdminFile() string {
	return filepath.Join(s.cfg.DataDir, ".run", "admin-account.json")
}

// provisionAppAdmin writes the Admin account for the main app. It is called
// at start-up and whenever the account is created.
func (s *Server) provisionAppAdmin() error {
	user, hash := s.auth.AdminCredential()
	if hash == "" {
		return nil
	}
	b, err := json.Marshal(struct {
		Username     string    `json:"username"`
		PasswordHash string    `json:"password_hash"`
		UpdatedAt    time.Time `json:"updated_at"`
	}{user, hash, s.now().UTC()})
	if err != nil {
		return err
	}
	path := s.appAdminFile()
	if old, err := os.ReadFile(path); err == nil && sameAccount(old, user, hash) {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func sameAccount(b []byte, user, hash string) bool {
	var cur struct {
		Username     string `json:"username"`
		PasswordHash string `json:"password_hash"`
	}
	return json.Unmarshal(b, &cur) == nil && cur.Username == user && cur.PasswordHash == hash
}
