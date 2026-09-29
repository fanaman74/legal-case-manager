# Case File Manager

A self-hosted, multi-user legal case file manager. It organises case material, converts it to AI-ready Markdown, indexes it for hybrid search, and answers questions with citations. Case data stays on your own computer; nothing goes to an external AI or embedding provider without a visible confirmation.

**Status:** phase 1 of 7: the launcher, Control Center and setup wizard skeleton. See [docs/design](docs/design) for the approved design brief and architecture note.

## How it fits together

Everything runs as ordinary Windows programs. There is no Docker.

```
Windows host
├── launcher.exe  (Windows service "CaseFileManagerLauncher", LocalSystem, starts at boot)
│     ├── Control Center at https://localhost:8443 (Admin only)
│     ├── its own login, one-time setup code, hash-chained audit log
│     └── starts and stops only the three services below, by name
├── CaseFiles-api     web app (Python)        runs as NT SERVICE\CaseFiles-api
├── CaseFiles-worker  background worker       runs as NT SERVICE\CaseFiles-worker (no internet)
└── CaseFiles-models  local AI models (Ollama) runs as NT SERVICE\CaseFiles-models
```

Each app service is hosted by `launcher.exe host <service>` under its own virtual account, with access only to the folders it needs. See [docs/design/architecture-note.md](docs/design/architecture-note.md).

## Repository layout

| Path | What |
|---|---|
| `launcher/` | Go launcher: allow-list, auth, audit, service supervisor, health checks, HTTPS server |
| `control-center/` | React Control Center, built into the launcher binary |
| `services/api/` | FastAPI web app and background worker (phase 1: health only). Its Dockerfile is only for a future Railway deployment. |
| `scripts/` | Build script, Windows installer, pinned runtime downloads (`runtimes.json`), CI smoke test |
| `docs/` | Design brief, tokens, architecture, install and certificate guides |

## Install

See [docs/install-windows.md](docs/install-windows.md).

## Develop

```bash
# Launcher tests (allow-list, auth, audit chain, network guard, CSRF, supervisor)
cd launcher && go test -race ./...

# Control Center
cd control-center && npm ci && npm run build && npm test

# API tests
cd services/api && pip install -r requirements-dev.txt && pytest

# Build everything (add "windows" for the install folder)
scripts/build.sh
```

To run locally on Linux or macOS, create a virtualenv with `services/api/requirements.txt`, then write a `launcher.json` such as:

```json
{"app_dir": "services/api", "python": ".venv/bin/python", "ollama": "/usr/local/bin/ollama",
 "tesseract": "/usr/bin/tesseract", "supervisor": "direct", "app_port": 9443}
```

and run `dist/launcher --config launcher.json`. In `direct` mode the launcher runs the services as its own child processes.

After changing `services/api/requirements.txt`, regenerate the hash-pinned Windows lock file:

```bash
cd services/api && uv pip compile requirements.txt --generate-hashes \
  --python-platform x86_64-pc-windows-msvc --python-version 3.13 --no-header -o requirements-windows.txt
```
