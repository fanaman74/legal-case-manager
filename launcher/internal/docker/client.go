// Package docker is a minimal, read-only Docker Engine API client. It can
// list, inspect, read stats and read logs for containers in one compose
// project. It deliberately has no methods that create, exec into or modify
// containers; state changes go through the compose package.
package docker

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Client talks to the local Docker Engine.
type Client struct {
	http    *http.Client
	project string
}

// New returns a client for the given compose project using the platform's
// default local endpoint (named pipe on Windows, unix socket elsewhere).
func New(project string) *Client {
	return NewWithDialer(project, dialLocal)
}

// NewWithDialer is used by tests to point the client at a fake engine.
func NewWithDialer(project string, dial func(ctx context.Context) (net.Conn, error)) *Client {
	tr := &http.Transport{
		DialContext:  func(ctx context.Context, _, _ string) (net.Conn, error) { return dial(ctx) },
		MaxIdleConns: 4,
	}
	return &Client{http: &http.Client{Transport: tr, Timeout: 15 * time.Second}, project: project}
}

// ErrUnavailable means the engine could not be reached.
var ErrUnavailable = errors.New("docker engine unavailable")

func (c *Client) get(ctx context.Context, path string, q url.Values, out any) error {
	u := "http://docker/v1.43" + path
	if len(q) > 0 {
		u += "?" + q.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return ErrNotFound
	}
	if resp.StatusCode >= 300 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("docker %s: %s: %s", path, resp.Status, strings.TrimSpace(string(b)))
	}
	if out == nil {
		return nil
	}
	if w, ok := out.(io.Writer); ok {
		_, err = io.Copy(w, io.LimitReader(resp.Body, 8<<20))
		return err
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// ErrNotFound means the container does not exist.
var ErrNotFound = errors.New("not found")

// Version is the engine version.
type Version struct {
	Version    string `json:"Version"`
	APIVersion string `json:"ApiVersion"`
	OS         string `json:"Os"`
}

// Version pings the engine and returns its version.
func (c *Client) Version(ctx context.Context) (Version, error) {
	var v Version
	err := c.get(ctx, "/version", nil, &v)
	return v, err
}

// Container is the subset of a container summary the launcher uses.
type Container struct {
	ID     string            `json:"Id"`
	Labels map[string]string `json:"Labels"`
}

// ComposeService returns the compose service key for the container.
func (ct Container) ComposeService() string {
	return ct.Labels["com.docker.compose.service"]
}

// List returns every container (running or not) in the compose project.
func (c *Client) List(ctx context.Context) ([]Container, error) {
	filters, _ := json.Marshal(map[string][]string{
		"label": {"com.docker.compose.project=" + c.project},
	})
	var out []Container
	err := c.get(ctx, "/containers/json", url.Values{"all": {"1"}, "filters": {string(filters)}}, &out)
	if err != nil {
		return nil, err
	}
	// Defence in depth: never trust the filter alone.
	kept := out[:0]
	for _, ct := range out {
		if ct.Labels["com.docker.compose.project"] == c.project {
			kept = append(kept, ct)
		}
	}
	return kept, nil
}

// Inspect is the subset of container details the launcher uses.
type Inspect struct {
	ID           string `json:"Id"`
	RestartCount int    `json:"RestartCount"`
	State        struct {
		Status     string    `json:"Status"`
		Running    bool      `json:"Running"`
		Restarting bool      `json:"Restarting"`
		OOMKilled  bool      `json:"OOMKilled"`
		ExitCode   int       `json:"ExitCode"`
		Error      string    `json:"Error"`
		StartedAt  time.Time `json:"StartedAt"`
		FinishedAt time.Time `json:"FinishedAt"`
		Health     *struct {
			Status string `json:"Status"`
			Log    []struct {
				End      time.Time `json:"End"`
				ExitCode int       `json:"ExitCode"`
				Output   string    `json:"Output"`
			} `json:"Log"`
		} `json:"Health"`
	} `json:"State"`
	Config struct {
		Image  string            `json:"Image"`
		Labels map[string]string `json:"Labels"`
	} `json:"Config"`
}

// Inspect returns details for a container that List returned.
func (c *Client) Inspect(ctx context.Context, id string) (Inspect, error) {
	var out Inspect
	err := c.get(ctx, "/containers/"+url.PathEscape(id)+"/json", nil, &out)
	if err == nil && out.Config.Labels["com.docker.compose.project"] != c.project {
		return Inspect{}, fmt.Errorf("container %s is not in project %s", id, c.project)
	}
	return out, err
}

// Usage is CPU and memory use for one container.
type Usage struct {
	CPUPercent  float64 `json:"cpuPercent"`
	MemoryBytes uint64  `json:"memoryBytes"`
	MemoryLimit uint64  `json:"memoryLimit"`
}

type statsWire struct {
	CPUStats    cpuStats `json:"cpu_stats"`
	PreCPUStats cpuStats `json:"precpu_stats"`
	MemoryStats struct {
		Usage uint64            `json:"usage"`
		Limit uint64            `json:"limit"`
		Stats map[string]uint64 `json:"stats"`
	} `json:"memory_stats"`
}

type cpuStats struct {
	CPUUsage struct {
		TotalUsage uint64 `json:"total_usage"`
	} `json:"cpu_usage"`
	SystemUsage uint64 `json:"system_cpu_usage"`
	OnlineCPUs  uint64 `json:"online_cpus"`
}

// Stats samples CPU and memory once (the engine takes two readings).
func (c *Client) Stats(ctx context.Context, id string) (Usage, error) {
	var s statsWire
	if err := c.get(ctx, "/containers/"+url.PathEscape(id)+"/stats", url.Values{"stream": {"false"}}, &s); err != nil {
		return Usage{}, err
	}
	return usageFrom(s), nil
}

func usageFrom(s statsWire) Usage {
	var u Usage
	cpuDelta := float64(s.CPUStats.CPUUsage.TotalUsage) - float64(s.PreCPUStats.CPUUsage.TotalUsage)
	sysDelta := float64(s.CPUStats.SystemUsage) - float64(s.PreCPUStats.SystemUsage)
	cpus := float64(s.CPUStats.OnlineCPUs)
	if cpus == 0 {
		cpus = 1
	}
	if cpuDelta > 0 && sysDelta > 0 {
		u.CPUPercent = cpuDelta / sysDelta * cpus * 100
	}
	mem := s.MemoryStats.Usage
	// Match `docker stats`: exclude page cache.
	if v, ok := s.MemoryStats.Stats["inactive_file"]; ok && v < mem {
		mem -= v
	}
	u.MemoryBytes = mem
	u.MemoryLimit = s.MemoryStats.Limit
	return u
}

// LogLine is one line of container output.
type LogLine struct {
	Time   string `json:"time"`
	Stream string `json:"stream"`
	Text   string `json:"text"`
}

// Logs returns the last tail lines of a container's output.
func (c *Client) Logs(ctx context.Context, id string, tail int) ([]LogLine, error) {
	var buf strings.Builder
	q := url.Values{"stdout": {"1"}, "stderr": {"1"}, "timestamps": {"1"}, "tail": {strconv.Itoa(tail)}}
	if err := c.get(ctx, "/containers/"+url.PathEscape(id)+"/logs", q, &buf); err != nil {
		return nil, err
	}
	return demux([]byte(buf.String())), nil
}

// demux splits Docker's multiplexed log stream (8-byte frame headers). A
// container with a TTY sends raw text instead, which is handled too.
func demux(b []byte) []LogLine {
	var lines []LogLine
	add := func(stream string, chunk []byte) {
		for _, l := range strings.Split(strings.TrimRight(string(chunk), "\n"), "\n") {
			if l == "" {
				continue
			}
			ts, text, found := strings.Cut(l, " ")
			if !found {
				ts, text = "", l
			}
			lines = append(lines, LogLine{Time: ts, Stream: stream, Text: text})
		}
	}
	for len(b) >= 8 && (b[0] == 1 || b[0] == 2) && b[1] == 0 && b[2] == 0 && b[3] == 0 {
		n := int(binary.BigEndian.Uint32(b[4:8]))
		if 8+n > len(b) {
			break
		}
		stream := "stdout"
		if b[0] == 2 {
			stream = "stderr"
		}
		add(stream, b[8:8+n])
		b = b[8+n:]
	}
	if len(b) > 0 {
		add("stdout", b)
	}
	return lines
}
