//go:build !windows

package procs

import (
	"os/exec"
	"syscall"
)

// platform on Unix puts the child in its own process group so the whole
// group can be signalled.
type platform struct{}

func (platform) prepare(cmd *exec.Cmd) {
	cmd.SysProcAttr = sysProcAttr()
}

func (platform) attach(*exec.Cmd) error { return nil }
func (platform) release()               {}

func (platform) terminate(cmd *exec.Cmd) { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM) }
func (platform) kill(cmd *exec.Cmd)      { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
