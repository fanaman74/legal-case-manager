//go:build windows

package docker

import (
	"context"
	"net"

	"github.com/Microsoft/go-winio"
)

func dialLocal(ctx context.Context) (net.Conn, error) {
	return winio.DialPipeContext(ctx, `\\.\pipe\docker_engine`)
}
