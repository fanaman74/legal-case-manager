#!/usr/bin/env bash
# Builds the Control Center and embeds it in the launcher.
#   scripts/build.sh            -> dist/launcher (this platform)
#   scripts/build.sh windows    -> dist/windows/ (launcher.exe, the app and install files)
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${VERSION:-0.1.0-dev}"

(cd control-center && npm ci && npm run build)
touch launcher/internal/web/dist/.gitkeep

LDFLAGS="-s -w -X github.com/fanaman74/legal-case-manager/launcher/internal/server.Version=${VERSION}"
if [[ "${1:-}" == "windows" ]]; then
  out=dist/windows
  rm -rf "$out" && mkdir -p "$out"
  (cd launcher && GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "$LDFLAGS" -o "../$out/launcher.exe" ./cmd/launcher)
  mkdir -p "$out/app"
  cp -r services/api/app services/api/requirements-windows.txt "$out/app/"
  find "$out/app" -name __pycache__ -prune -exec rm -rf {} +
  cp scripts/install.ps1 scripts/uninstall.ps1 "$out/"
  cp docs/install-windows.md "$out/README-install.md"
  cp docs/trust-certificate.md "$out/"
  echo "Built $out. Copy the folder to the Windows computer and run install.ps1 as Administrator."
else
  mkdir -p dist
  (cd launcher && go build -trimpath -ldflags "$LDFLAGS" -o ../dist/launcher ./cmd/launcher)
  echo "Built dist/launcher"
fi
