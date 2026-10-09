#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 20 || (major === 20 && minor < 19) || (major === 22 && minor < 12)) throw new Error("Use Node 20.19+, 22.12+, or 24+")'
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --cache-dir "$repo_root/.cache/pip" -r services/api/requirements.lock
cd "$repo_root/control-center"
npm --cache "$repo_root/.cache/npm" ci --no-fund
npm --cache "$repo_root/.cache/npm" run build
