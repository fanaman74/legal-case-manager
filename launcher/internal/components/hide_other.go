//go:build !windows

package components

import "os/exec"

func hideWindow(*exec.Cmd) {}
