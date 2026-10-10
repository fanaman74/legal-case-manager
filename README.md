# Case File Manager

A self-hosted, multi-user legal case file manager. It organises case material, converts it to AI-ready Markdown, indexes it for hybrid search, and answers questions with citations. Case data stays on your own computer; nothing goes to an external AI or embedding provider without a visible confirmation.

**Status:** phase 2 of 7 in progress: accounts, roles, cases and file upload (API done; screens follow the [phase 2 screen plan](docs/design/phase2-screen-plan.md) once approved). Phase 1 (launcher, Control Center, setup wizard) is done. See [docs/design](docs/design) for the approved design brief and architecture note.

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
| `launcher/` | Go launcher: allow-list, auth, audit, service supervisor, health checks, component installer with pinned downloads (`internal/components/runtimes.json`), HTTPS server |
| `control-center/` | React Control Center, built into the launcher binary |
| `services/api/` | FastAPI web app and background worker. The app has sign-in, people and roles, cases, file upload and a hash-chained audit log, all in `app.db` (SQLite) with originals under `data/cases/<case-id>/original/`. Case access is checked on the server for every route (`app/access.py`). |
| `scripts/` | Build script and the Windows CI smoke test. The Windows installer (Setup.exe) is `launcher/internal/setup`. |
| `docs/` | Design brief, tokens, architecture, install and certificate guides |

## Accounts

The Admin account is created once, in the Control Center's setup wizard. The launcher hands its username and password hash to the web app (`data/.run/admin-account.json`), so the Admin signs in to both with the same password, and changes it in the Control Center. The Admin adds everyone else in the web app as Editor or Read-only and assigns them to cases; each person gets a temporary password and chooses their own at first sign-in.

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
