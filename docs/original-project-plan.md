# Case File Manager

A self-hosted, multi-user legal case file manager. It organises case material, converts it to AI-ready Markdown, indexes it for hybrid search, and answers questions with citations. Case data stays on your own computer; nothing goes to an external AI or embedding provider without a visible confirmation.

**Status:** phase 1 of 7: the launcher, Control Center and setup wizard skeleton. See [docs/design](docs/design) for the approved design brief and architecture note.

## How it fits together

```
Windows host
├── launcher.exe (Windows service, always on)
│     ├── Control Center at https://localhost:8443 (Admin only)
│     ├── its own login, one-time setup code, hash-chained audit log
│     └── runs only allow-listed `docker compose` actions
└── Docker Compose project "casefiles"
      api · worker · queue · ocr · pst · models
```

The launcher runs on the host, never in a container, so no container ever gets the Docker socket. See [docs/design/architecture-note.md](docs/design/architecture-note.md).

## Repository layout

| Path | What |
|---|---|
| `launcher/` | Go launcher: allow-list, auth, audit, health checks, HTTPS server |
| `control-center/` | React Control Center, built into the launcher binary |
| `services/api/` | FastAPI web app and background worker (phase 1: health only) |
| `services/tools/` | OCR and PST parser containers (phase 1: health only) |
| `deploy/compose.yaml` | Service definitions |
| `scripts/` | Build script and Windows installer |
| `docs/` | Design brief, tokens, architecture, install and certificate guides |

## Install

See [docs/install-windows.md](docs/install-windows.md).

## Develop

```bash
# Launcher tests (allow-list, auth, audit chain, network guard, CSRF)
cd launcher && go test -race ./...

# Control Center
cd control-center && npm ci && npm run build && npm test

# API tests
cd services/api && pip install -r requirements-dev.txt && pytest

# Build everything (add "windows" for the install folder)
scripts/build.sh
```

To run locally on Linux or macOS, write a `launcher.json` with `data_dir`, `state_dir`, `certs_dir` and `compose_file`, a matching `deploy/.env` (see `deploy/.env.example`), then run `dist/launcher --config launcher.json`. The containers run as uid 10001, so the data folder must be writable by that user.
