#!/usr/bin/env bash
# Builds the Control Center (embedded in the launcher) and the web app's
# screens (served by the API from services/api/app/web).
#   scripts/build.sh            -> dist/launcher (this platform)
#   scripts/build.sh windows    -> dist/windows/ (Setup.exe, the app and the install notes)
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${VERSION:-0.1.0-dev}"

(cd control-center && npm ci && npm run build)
(cd web && npm ci && npm run build)
touch launcher/internal/web/dist/.gitkeep

LDFLAGS="-s -w -X github.com/fanaman74/legal-case-manager/launcher/internal/server.Version=${VERSION}"
if [[ "${1:-}" == "windows" ]]; then
  out=dist/windows
  rm -rf "$out" && mkdir -p "$out"
  (cd launcher && GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "$LDFLAGS" -o "../$out/Setup.exe" ./cmd/launcher)
  mkdir -p "$out/app"
  cp -r services/api/app services/api/requirements-windows.txt "$out/app/"
  find "$out/app" -name __pycache__ -prune -exec rm -rf {} +
  cp docs/install-windows.md "$out/README-install.md"
  cp docs/trust-certificate.md "$out/"
  echo "Built $out. Copy the folder to the Windows computer and double-click Setup.exe."
else
  mkdir -p dist
  (cd launcher && go build -trimpath -ldflags "$LDFLAGS" -o ../dist/launcher ./cmd/launcher)
  echo "Built dist/launcher"
fi
