// Package status turns process state and health checks into the four states
// the Control Center shows, plus a plain-language explanation when something
// is wrong.
package status

import (
	"fmt"
	"strings"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/health"
	"github.com/fanaman74/legal-case-manager/launcher/internal/supervisor"
	"github.com/fanaman74/legal-case-manager/launcher/internal/sysinfo"
)

// State is what the Admin sees.
type State string

const (
	Running  State = "running"
	Starting State = "starting"
	Stopped  State = "stopped"
	Error    State = "error"
)

// Problem explains a failure: what happened, why it probably happened, and
// the next step. FixAction, when set, is an allow-listed action that is safe
// to offer as a button.
type Problem struct {
	What      string             `json:"what"`
	Why       string             `json:"why"`
	Next      string             `json:"next"`
	FixAction catalog.ActionName `json:"fixAction,omitempty"`
	FixLabel  string             `json:"fixLabel,omitempty"`
}

// Service is the Control Center view of one service.
type Service struct {
	catalog.Service
	State        State          `json:"state"`
	Detail       string         `json:"detail"`
	StartedAt    *time.Time     `json:"startedAt,omitempty"`
	Version      string         `json:"version"`
	Usage        *sysinfo.Usage `json:"usage,omitempty"`
	LastCheck    *time.Time     `json:"lastCheck,omitempty"`
	RestartCount int            `json:"restartCount"`
	AutoRestart  *time.Time     `json:"autoRestartAt,omitempty"`
	Problem      *Problem       `json:"problem,omitempty"`
}

// StartGrace is how long a newly started service may take to pass its first
// health check before it counts as not responding. Loading the models can
// take a while on a slow disk.
var StartGrace = map[catalog.ServiceID]time.Duration{
	catalog.API:    45 * time.Second,
	catalog.Worker: 30 * time.Second,
	catalog.Models: 90 * time.Second,
}

func restartFix(svc catalog.Service) (catalog.ActionName, string) {
	return catalog.ServiceRestart, "Restart " + svc.Name
}

// Derive computes what the Admin sees for svc. h is the latest health check,
// or nil if the service wasn't probed.
func Derive(svc catalog.Service, info supervisor.Info, h *health.Result, now time.Time) Service {
	out := Service{Service: svc, RestartCount: info.Restarts, AutoRestart: info.LastRestartAt}
	if h != nil {
		t := h.At
		out.LastCheck = &t
		out.Version = h.Version
	}
	fix, fixLabel := restartFix(svc)

	switch {
	case info.Missing:
		out.State, out.Detail = Error, "Not installed"
		out.Problem = &Problem{
			What: svc.Name + " can't start because its program is missing.",
			Why:  "Part of the installation is missing or was removed, often by antivirus software.",
			Next: "Run install.ps1 again as administrator. It puts back the missing files and keeps your cases.",
		}
		return out
	case info.GaveUp && info.StartErr != "":
		out.State, out.Detail = Error, "Couldn't start"
		out.Problem = &Problem{
			What: svc.Name + " couldn't start.", Why: "Windows reported: " + firstLine(info.StartErr) + ".",
			Next: "Try starting it again. If it fails again, download diagnostics and run install.ps1 again.",
			FixAction: fix, FixLabel: fixLabel,
		}
		return out
	case info.GaveUp:
		code := ""
		if info.LastExit != nil {
			code = fmt.Sprintf(" (code %d)", info.LastExit.Code)
		}
		out.State, out.Detail = Error, "Keeps stopping"+code
		out.Problem = &Problem{
			What:      svc.Name + " stopped " + fmt.Sprint(info.Restarts+1) + " times in a few minutes, so the launcher stopped restarting it.",
			Why:       "It exits with an error as soon as it starts. The last lines of its log usually say why.",
			Next:      "Open its log below. When the cause is fixed, restart it. If you can't tell, download diagnostics.",
			FixAction: fix, FixLabel: fixLabel,
		}
		return out
	}

	switch info.Phase {
	case supervisor.Stopped:
		out.State, out.Detail = Stopped, "Stopped"
		if info.LastExit == nil {
			out.Detail = "Not started yet"
		}
		out.LastCheck, out.Version, out.AutoRestart = nil, "", nil
	case supervisor.Stopping:
		out.State, out.Detail = Starting, "Stopping"
	case supervisor.Starting:
		out.State, out.Detail = Starting, "Starting up"
		if info.Restarts > 0 {
			out.Detail = "Restarting after a crash"
		}
	case supervisor.Running:
		out.StartedAt = info.StartedAt
		switch {
		case h != nil && h.OK:
			out.State, out.Detail = Running, "Running"
		case info.StartedAt != nil && now.Sub(*info.StartedAt) < StartGrace[svc.ID]:
			out.State, out.Detail = Starting, "Starting up"
		default:
			why := "It may still be loading, or it hit an internal error."
			if h != nil && h.Detail != "" {
				why = "Its health check says " + h.Detail + ". " + why
			}
			out.State, out.Detail = Error, "Not responding"
			out.Problem = &Problem{
				What:      svc.Name + " is running but not responding to health checks.",
				Why:       why,
				Next:      "Restart it. If this keeps happening, open its log below and download diagnostics.",
				FixAction: fix, FixLabel: fixLabel,
			}
		}
	default:
		out.State, out.Detail = Error, "Unknown state"
		out.Problem = &Problem{
			What: svc.Name + " is in a state the launcher doesn't recognise.",
			Why:  "Windows reported an unexpected service state.",
			Next: "Restart it. If that doesn't help, restart the computer.",
			FixAction: fix, FixLabel: fixLabel,
		}
	}
	return out
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	return s
}

// RuntimeProblem is shown when services can't be run at all.
func RuntimeProblem(reason string) Problem {
	return Problem{
		What: "The app services can't be started.",
		Why:  reason,
		Next: "Run install.ps1 again as administrator. It repairs the installation and keeps your cases. This page updates by itself.",
	}
}
