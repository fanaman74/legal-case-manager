// Package compose turns a validated action into a fixed `docker compose`
// argument list and runs it without a shell. The only values that reach the
// argument list are the compose file path and project name from the
// launcher's own config, and service keys from the catalog.
package compose

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"strings"

	"github.com/fanaman74/legal-case-manager/launcher/internal/actions"
	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

// Project identifies the compose project the launcher manages.
type Project struct {
	File string
	Name string
}

// Args returns the docker CLI arguments for a stack or service action. It
// returns false for actions that are not compose operations.
func (p Project) Args(r actions.Request) ([]string, bool) {
	base := []string{"compose", "--project-name", p.Name, "--file", p.File}
	switch r.Action {
	case catalog.StackStartAll:
		return append(base, "up", "--detach", "--wait", "--wait-timeout", "600"), true
	case catalog.StackStopAll:
		return append(base, "stop"), true
	case catalog.ServiceStart:
		return append(base, "up", "--detach", "--no-deps", r.Service.ComposeService), true
	case catalog.ServiceStop:
		return append(base, "stop", r.Service.ComposeService), true
	case catalog.ServiceRestart:
		// up --force-recreate also recovers a container that was removed.
		return append(base, "up", "--detach", "--no-deps", "--force-recreate", r.Service.ComposeService), true
	}
	return nil, false
}

// Runner executes docker with the given arguments.
type Runner interface {
	Run(ctx context.Context, args []string) (string, error)
}

// ExecRunner runs the docker binary directly (no shell).
type ExecRunner struct {
	Docker string
}

func (e ExecRunner) Run(ctx context.Context, args []string) (string, error) {
	cmd := exec.CommandContext(ctx, e.Docker, args...)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	s := strings.TrimSpace(out.String())
	if err != nil {
		return s, fmt.Errorf("docker compose failed: %w", err)
	}
	return s, nil
}
