// Package web embeds the built Control Center (control-center/ → dist/).
package web

import (
	"embed"
	"io/fs"
)

//go:embed all:dist
var dist embed.FS

// FS returns the built files. It contains only .gitkeep until the frontend
// has been built with scripts/build.sh.
func FS() fs.FS {
	sub, _ := fs.Sub(dist, "dist")
	return sub
}
