//go:build !windows

package docker

import (
	"context"
	"net"
	"os"
	"strings"
)

func dialLocal(ctx context.Context) (net.Conn, error) {
	path := "/var/run/docker.sock"
	if h := os.Getenv("DOCKER_HOST"); strings.HasPrefix(h, "unix://") {
		path = strings.TrimPrefix(h, "unix://")
	}
	var d net.Dialer
	return d.DialContext(ctx, "unix", path)
}
