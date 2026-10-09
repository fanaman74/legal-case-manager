#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root/control-center"
npm --cache "$repo_root/.cache/npm" run build
