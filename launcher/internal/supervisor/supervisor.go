// Package supervisor starts, stops and reports on the app services. Two
// implementations share one interface:
//
//   - Windows (the default on Windows): each app process is its own Windows
//     service running under a virtual account (NT SERVICE\CaseFiles-api and
//     so on), hosted by `launcher host <service>`. The launcher controls them
//     through the Service Control Manager, so they keep running if the
//     launcher restarts, never run as SYSTEM, and can be given their own file
//     permissions and firewall rules.
//   - Direct (development and CI): the launcher runs the processes itself.
package supervisor

import (
	"context"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
)

// Phase is the supervisor's view of a service, before health checks.
type Phase string

const (
	Stopped  Phase = "stopped"
	Starting Phase = "starting"
	Running  Phase = "running"
	Stopping Phase = "stopping"
)

// Info is one service's process state.
type Info struct {
	procs.Status
	Phase Phase `json:"phase"`
}

// Supervisor runs the catalog services.
type Supervisor interface {
	// Name says how services are run, for the Control Center.
	Name() string
	// Ready returns nil if services can be started, or a plain-language
	// reason they can't.
	Ready(ctx context.Context) error
	Start(ctx context.Context, id catalog.ServiceID) error
	Stop(ctx context.Context, id catalog.ServiceID) error
	Info(ctx context.Context, id catalog.ServiceID) (Info, error)
}
