//go:build !windows

package setup

import "errors"

var errWindowsOnly = errors.New("setup is only available on Windows; use `launcher run` for development")

// DoubleClick is Windows-only.
func DoubleClick() error { return errWindowsOnly }

// Run is Windows-only.
func Run(Options) error { return errWindowsOnly }

// Uninstall is Windows-only.
func Uninstall(Options) error { return errWindowsOnly }
