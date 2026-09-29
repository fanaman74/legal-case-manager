//go:build !windows

// Package winsvc runs the launcher as a Windows service. On other platforms
// the launcher runs in the foreground (development and tests).
package winsvc

import (
	"context"
	"errors"
)

// Name is the launcher's Windows service name.
const Name = "CaseFileManagerLauncher"

// IsService is always false outside Windows.
func IsService() bool { return false }

// Run is not used outside Windows.
func Run(_ string, run func(ctx context.Context) error) error { return run(context.Background()) }

// Install is Windows-only.
func Install(string) error { return errors.New("service install is only available on Windows") }

// Uninstall is Windows-only.
func Uninstall() error { return errors.New("service uninstall is only available on Windows") }
