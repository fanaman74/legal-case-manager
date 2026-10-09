#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"
if [ ! -f control-center/dist/index.html ] || [ ! -x .venv/bin/python ]; then
  echo 'Run bash scripts/setup.sh before starting the workspace.' >&2
  exit 1
fi
exec .venv/bin/python -m uvicorn app:app --app-dir services/api --host 127.0.0.1 --port ${CASEFILES_PORT:-8000}
