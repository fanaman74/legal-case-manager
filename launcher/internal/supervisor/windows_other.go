//go:build !windows

package supervisor

import (
	"errors"

	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
)

// NewWindows is only available on Windows.
func NewWindows(config.Config) (Supervisor, error) {
	return nil, errors.New(`the "windows" supervisor only runs on Windows; set "supervisor": "direct"`)
}
