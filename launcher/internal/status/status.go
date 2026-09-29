// Package status turns raw container details into the four states the
// Control Center shows, plus a plain-language explanation when something is
// wrong.
package status

import (
	"fmt"
	"strings"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/docker"
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
	State        State         `json:"state"`
	Detail       string        `json:"detail"`
	StartedAt    *time.Time    `json:"startedAt,omitempty"`
	Version      string        `json:"version"`
	Usage        *docker.Usage `json:"usage,omitempty"`
	LastCheck    *time.Time    `json:"lastCheck,omitempty"`
	RestartCount int           `json:"restartCount"`
	AutoRestart  *time.Time    `json:"autoRestartAt,omitempty"`
	Problem      *Problem      `json:"problem,omitempty"`
}

// Derive computes the state of svc from its container. info is nil when the
// container does not exist yet.
func Derive(svc catalog.Service, info *docker.Inspect, polledAt time.Time) Service {
	out := Service{Service: svc}
	if info == nil {
		out.State = Stopped
		out.Detail = "Not started yet"
		return out
	}
	out.Version = version(info)
	out.RestartCount = info.RestartCount
	if !info.State.StartedAt.IsZero() && info.State.Running {
		t := info.State.StartedAt
		out.StartedAt = &t
	}
	out.LastCheck = &polledAt
	health := ""
	if h := info.State.Health; h != nil {
		health = h.Status
		if n := len(h.Log); n > 0 && !h.Log[n-1].End.IsZero() {
			t := h.Log[n-1].End
			out.LastCheck = &t
		}
	}

	switch info.State.Status {
	case "running":
		switch health {
		case "", "healthy":
			out.State, out.Detail = Running, "Running"
		case "starting":
			out.State, out.Detail = Starting, "Starting up"
		default:
			out.State, out.Detail = Error, "Not responding"
			out.Problem = &Problem{
				What:      svc.Name + " is running but not responding to health checks.",
				Why:       "It may still be loading, or it hit an internal error." + lastHealthOutput(info),
				Next:      "Restart it. If this keeps happening, download diagnostics from the Logs section.",
				FixAction: catalog.ServiceRestart,
				FixLabel:  "Restart " + svc.Name,
			}
		}
	case "restarting":
		out.State, out.Detail = Starting, "Restarting after a crash"
	case "created":
		out.State, out.Detail = Stopped, "Not started yet"
	case "paused":
		out.State, out.Detail = Stopped, "Paused"
	case "exited", "dead":
		out = exited(out, svc, info)
	default:
		out.State, out.Detail = Error, "Unknown state"
		out.Problem = &Problem{
			What:      svc.Name + " is in a state the launcher doesn't recognise (" + info.State.Status + ").",
			Why:       "Docker reported an unexpected status.",
			Next:      "Restart it. If that doesn't help, restart Docker Desktop.",
			FixAction: catalog.ServiceRestart,
			FixLabel:  "Restart " + svc.Name,
		}
	}
	return out
}

func exited(out Service, svc catalog.Service, info *docker.Inspect) Service {
	st := info.State
	switch {
	case st.OOMKilled:
		out.State, out.Detail = Error, "Ran out of memory"
		out.Problem = &Problem{
			What:      svc.Name + " stopped because it ran out of memory.",
			Why:       "Docker probably has too little memory for this job. Large PST files and OCR need the most.",
			Next:      "In Docker Desktop, open Settings › Resources and give Docker at least 6 GB of memory, then restart " + svc.Name + ".",
			FixAction: catalog.ServiceRestart,
			FixLabel:  "Restart " + svc.Name,
		}
	case st.Status == "exited" && st.ExitCode == 0:
		out.State, out.Detail = Stopped, "Stopped"
	case st.Status == "exited" && (st.ExitCode == 137 || st.ExitCode == 143):
		// Killed by a stop signal: treat as a normal stop.
		out.State, out.Detail = Stopped, "Stopped"
	default:
		out.State, out.Detail = Error, fmt.Sprintf("Stopped unexpectedly (code %d)", st.ExitCode)
		why := "It exited with an error. The last lines of its log usually say why."
		if st.Error != "" {
			why = "Docker reported: " + firstLine(st.Error) + "."
		}
		out.Problem = &Problem{
			What:      svc.Name + " stopped unexpectedly.",
			Why:       why,
			Next:      "Restart it. If it stops again, open its log below and download diagnostics.",
			FixAction: catalog.ServiceRestart,
			FixLabel:  "Restart " + svc.Name,
		}
	}
	return out
}

// version prefers the image tag, which is what compose.yaml pins; a base
// image's version label (e.g. an OS release) would be misleading.
func version(info *docker.Inspect) string {
	img := info.Config.Image
	if i := strings.LastIndex(img, ":"); i >= 0 && !strings.Contains(img[i:], "/") && img[i+1:] != "latest" {
		return img[i+1:]
	}
	if v := info.Config.Labels["org.opencontainers.image.version"]; v != "" {
		return v
	}
	return "latest"
}

func lastHealthOutput(info *docker.Inspect) string {
	h := info.State.Health
	if h == nil || len(h.Log) == 0 {
		return ""
	}
	out := firstLine(h.Log[len(h.Log)-1].Output)
	if out == "" {
		return ""
	}
	if len(out) > 160 {
		out = out[:160] + "…"
	}
	return " Last check said: " + out
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	return s
}

// DockerDown is the problem shown when the engine can't be reached.
var DockerDown = Problem{
	What: "Docker isn't running.",
	Why:  "Docker Desktop hasn't started yet, or it was closed. Every service runs inside Docker.",
	Next: "Open Docker Desktop and wait until it shows Engine running. This page updates by itself.",
}
