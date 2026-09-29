// Package checks runs the system checks shown in the Control Center and used
// by the setup wizard. Every failing check says what to do next.
package checks

import (
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/tlsca"
)

// Level is a check result.
type Level string

const (
	Pass    Level = "pass"
	Warn    Level = "warn"
	Fail    Level = "fail"
	Pending Level = "pending" // not applicable yet (e.g. before first start)
)

// Check is one row in System checks.
type Check struct {
	ID     string `json:"id"`
	Label  string `json:"label"`
	Level  Level  `json:"level"`
	Detail string `json:"detail"`
	Next   string `json:"next,omitempty"`
}

// Disk thresholds.
const (
	DiskFailBytes = 5 << 30
	DiskWarnBytes = 20 << 30
)

// Disk checks free space where case data lives.
func Disk(dataDir string) Check {
	c := Check{ID: "disk", Label: "Free disk space"}
	free, err := freeBytes(existingParent(dataDir))
	if err != nil {
		c.Level, c.Detail = Fail, "Couldn't read free space for "+dataDir+"."
		c.Next = "Check that the data folder's drive is connected."
		return c
	}
	c.Detail = fmt.Sprintf("%s free on the data drive", HumanBytes(free))
	switch {
	case free < DiskFailBytes:
		c.Level, c.Next = Fail, "Free up space on this drive. The app needs at least 5 GB free to convert and index files."
	case free < DiskWarnBytes:
		c.Level, c.Next = Warn, "Space is getting low. Large PST files need several times their own size while indexing."
	default:
		c.Level = Pass
	}
	return c
}

func existingParent(p string) string {
	for {
		if _, err := os.Stat(p); err == nil {
			return p
		}
		parent := filepath.Dir(p)
		if parent == p {
			return p
		}
		p = parent
	}
}

// HumanBytes formats a byte count.
func HumanBytes(b uint64) string {
	const unit = 1024
	if b < unit {
		return fmt.Sprintf("%d B", b)
	}
	div, exp := uint64(unit), 0
	for n := b / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(b)/float64(div), "KMGTPE"[exp])
}

// Docker reports whether the engine answered.
func Docker(version string, err error) Check {
	c := Check{ID: "docker", Label: "Docker"}
	if err != nil {
		c.Level, c.Detail = Fail, "Docker isn't running."
		c.Next = "Open Docker Desktop and wait until it shows Engine running."
		return c
	}
	c.Level, c.Detail = Pass, "Running (engine "+version+")"
	return c
}

// Port checks that the web app's port is free, or already held by the app.
func Port(port int, heldByApp bool) Check {
	c := Check{ID: "port", Label: "Web app port " + strconv.Itoa(port)}
	if heldByApp {
		c.Level, c.Detail = Pass, "In use by the web app"
		return c
	}
	ln, err := net.Listen("tcp", ":"+strconv.Itoa(port))
	if err != nil {
		c.Level, c.Detail = Fail, "Another program is using port "+strconv.Itoa(port)+"."
		c.Next = "Close the other program (often IIS, Skype or another web server), or ask for the app to use a different port."
		return c
	}
	ln.Close()
	c.Level, c.Detail = Pass, "Free"
	return c
}

// Model checks that the embedding model files are on disk.
func Model(dataDir, model string) Check {
	c := Check{ID: "model", Label: "Embedding model"}
	manifest := filepath.Join(dataDir, "models", "models", "manifests", "registry.ollama.ai", "library", model)
	if _, err := os.Stat(manifest); err == nil {
		c.Level, c.Detail = Pass, model+" is downloaded"
		return c
	}
	c.Level, c.Detail = Pending, model+" isn't downloaded yet."
	c.Next = "The setup wizard downloads it in step 4."
	return c
}

// OCR reflects the OCR service's health.
func OCR(running bool) Check {
	c := Check{ID: "ocr", Label: "OCR"}
	if running {
		c.Level, c.Detail = Pass, "Installed and responding"
		return c
	}
	c.Level, c.Detail = Pending, "Available once the OCR service is running."
	c.Next = "Start the OCR service above."
	return c
}

// Certificate checks the HTTPS certificate's expiry and addresses.
func Certificate(certDir string, lan []net.IP, now time.Time) Check {
	c := Check{ID: "cert", Label: "HTTPS certificate"}
	info, err := tlsca.ReadServer(certDir)
	if err != nil {
		c.Level, c.Detail = Fail, "No HTTPS certificate found."
		c.Next = "Renew the certificate. If that fails, run the installer again."
		return c
	}
	left := info.NotAfter.Sub(now)
	c.Detail = "Valid until " + info.NotAfter.Format("2 Jan 2006")
	switch {
	case left <= 0:
		c.Level, c.Detail = Fail, "Expired on "+info.NotAfter.Format("2 Jan 2006")
		c.Next = "Renew the certificate. Other devices keep trusting it because the local CA doesn't change."
		return c
	case left < 30*24*time.Hour:
		c.Level, c.Next = Warn, fmt.Sprintf("Expires in %d days. Renew it now to avoid browser warnings.", int(left.Hours()/24))
		return c
	}
	for _, ip := range lan {
		if !info.Covers(ip) {
			c.Level = Warn
			c.Detail += ", but it doesn't cover " + ip.String()
			c.Next = "This computer's network address changed. Renew the certificate, and reserve this address in your router so it stays the same."
			return c
		}
	}
	c.Level = Pass
	return c
}

// Database reports whether the app database exists yet.
func Database(dataDir string) Check {
	c := Check{ID: "database", Label: "Database"}
	st, err := os.Stat(filepath.Join(dataDir, "app.db"))
	if err != nil {
		c.Level, c.Detail = Pending, "Not created yet. The web app creates it the first time it starts."
		return c
	}
	c.Level, c.Detail = Pass, HumanBytes(uint64(st.Size()))
	return c
}

// VectorIndex reports whether any case has been indexed.
func VectorIndex(dataDir string) Check {
	c := Check{ID: "vectors", Label: "Search index"}
	matches, _ := filepath.Glob(filepath.Join(dataDir, "cases", "*", "vectors"))
	if len(matches) == 0 {
		c.Level, c.Detail = Pending, "No cases indexed yet."
		return c
	}
	c.Level, c.Detail = Pass, fmt.Sprintf("%d cases indexed", len(matches))
	return c
}

// AuditChain verifies the audit log.
func AuditChain(path string) Check {
	c := Check{ID: "audit", Label: "Audit log"}
	n, err := audit.Verify(path)
	var t audit.ErrTampered
	switch {
	case errors.As(err, &t):
		c.Level = Fail
		c.Detail = fmt.Sprintf("Entry %d has been changed or removed.", t.Line)
		c.Next = "Keep a copy of the audit file for your records and download diagnostics. Don't delete the file."
	case err != nil:
		c.Level, c.Detail = Fail, "Couldn't read the audit log."
		c.Next = "Check the data folder's permissions."
	default:
		c.Level, c.Detail = Pass, fmt.Sprintf("Intact, %d entries", n)
	}
	return c
}
