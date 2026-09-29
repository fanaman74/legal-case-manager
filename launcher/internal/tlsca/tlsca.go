// Package tlsca creates a small local certificate authority and a server
// certificate for the host's names and LAN addresses. Other devices trust the
// CA certificate once (see docs/trust-certificate.md).
package tlsca

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"time"
)

// Files inside the certificate directory.
const (
	CAKey      = "ca.key"
	CACert     = "ca.crt"
	ServerKey  = "server.key"
	ServerCert = "server.crt"
)

// Leaf validity stays under the 398-day limit browsers enforce.
const leafValidity = 397 * 24 * time.Hour

// EnsureCA creates the CA if it doesn't exist yet.
func EnsureCA(dir string) error {
	if _, err := os.Stat(filepath.Join(dir, CACert)); err == nil {
		return nil
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	tmpl := &x509.Certificate{
		SerialNumber:          serial(),
		Subject:               pkix.Name{CommonName: "Case File Manager local CA", Organization: []string{"Case File Manager"}},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().AddDate(10, 0, 0),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
		MaxPathLenZero:        true,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return err
	}
	if err := writeKey(filepath.Join(dir, CAKey), key, 0o600); err != nil {
		return err
	}
	return writeCert(filepath.Join(dir, CACert), der)
}

// IssueServer (re)issues the server certificate for names and ips.
func IssueServer(dir string, names []string, ips []net.IP) error {
	caCert, caKey, err := loadCA(dir)
	if err != nil {
		return err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	tmpl := &x509.Certificate{
		SerialNumber: serial(),
		Subject:      pkix.Name{CommonName: names[0]},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(leafValidity),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:     names,
		IPAddresses:  ips,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, caCert, &key.PublicKey, caKey)
	if err != nil {
		return err
	}
	// The server key is bind-mounted read-only into the web app container,
	// which runs as a non-root user, so the file itself must be readable.
	// The certs folder (0700) keeps other users on the host out.
	if err := writeKey(filepath.Join(dir, ServerKey), key, 0o644); err != nil {
		return err
	}
	return writeCert(filepath.Join(dir, ServerCert), der)
}

// Info summarises the current server certificate.
type Info struct {
	NotAfter time.Time
	DNSNames []string
	IPs      []net.IP
}

// Covers reports whether the certificate is valid for ip.
func (i Info) Covers(ip net.IP) bool {
	for _, c := range i.IPs {
		if c.Equal(ip) {
			return true
		}
	}
	return false
}

// ReadServer parses the server certificate.
func ReadServer(dir string) (Info, error) {
	b, err := os.ReadFile(filepath.Join(dir, ServerCert))
	if err != nil {
		return Info{}, err
	}
	blk, _ := pem.Decode(b)
	if blk == nil {
		return Info{}, errors.New("server certificate is not PEM")
	}
	c, err := x509.ParseCertificate(blk.Bytes)
	if err != nil {
		return Info{}, err
	}
	return Info{NotAfter: c.NotAfter, DNSNames: c.DNSNames, IPs: c.IPAddresses}, nil
}

func loadCA(dir string) (*x509.Certificate, *ecdsa.PrivateKey, error) {
	cb, err := os.ReadFile(filepath.Join(dir, CACert))
	if err != nil {
		return nil, nil, err
	}
	kb, err := os.ReadFile(filepath.Join(dir, CAKey))
	if err != nil {
		return nil, nil, err
	}
	cblk, _ := pem.Decode(cb)
	kblk, _ := pem.Decode(kb)
	if cblk == nil || kblk == nil {
		return nil, nil, fmt.Errorf("CA files in %s are damaged", dir)
	}
	cert, err := x509.ParseCertificate(cblk.Bytes)
	if err != nil {
		return nil, nil, err
	}
	key, err := x509.ParseECPrivateKey(kblk.Bytes)
	if err != nil {
		return nil, nil, err
	}
	return cert, key, nil
}

func serial() *big.Int {
	n, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 127))
	return n
}

func writeKey(path string, key *ecdsa.PrivateKey, mode os.FileMode) error {
	der, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der}), mode); err != nil {
		return err
	}
	return os.Chmod(path, mode) // WriteFile keeps the old mode on overwrite
}

func writeCert(path string, der []byte) error {
	return os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o644)
}
