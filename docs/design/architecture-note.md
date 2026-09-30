# Architecture note: launcher security and vector store

Status: approved 2026-09-29; **revised the same day for no Docker** (fred: "I don't want to use Docker"). Companion to [design-brief.md](design-brief.md).
Items marked **Flag** are risks you asked me to call out.

---

## 1. Shape of the system

```
Windows host (no Docker)
├── Launcher             Windows service, LocalSystem, starts at boot
│     ├── serves the Control Center + its own auth
│     ├── starts/stops the three app services through the Service Control Manager
│     └── checks each service's health on loopback
├── CaseFiles-api        FastAPI web app       NT SERVICE\CaseFiles-api     HTTPS on the LAN (Private networks only)
├── CaseFiles-worker     conversion, OCR, PST,  NT SERVICE\CaseFiles-worker  outbound internet blocked
│                        chunking, embedding
└── CaseFiles-models     Ollama (embeddings,    NT SERVICE\CaseFiles-models  loopback only (127.0.0.1:11434)
                         optional local chat)
C:\CaseFiles\launcher  → launcher credentials, audit log, settings (Administrators/SYSTEM only)
C:\CaseFiles\certs     → local CA + server cert (web app may read server.crt/key only)
C:\CaseFiles\runtime   → Python 3.13 with the app's packages, Tesseract, Ollama
C:\CaseFiles\data      → web app and worker only; models service gets data\models only
      ├── app.db           SQLite: users, cases, files, chunks, FTS5, audit log, job queue
      └── cases/<case-id>/original | markdown | vectors
```

OCR (Tesseract) and PST parsing (libpff) are programs and libraries the worker calls, not long-running services, so the Control Center shows them as system checks that prove they actually run.

## 2. Launcher security model

A web page that can start and stop services is a privileged entry point, so the launcher is built to do very little, very carefully.

**2.1 Where it runs, and who the app runs as.** The launcher is a Windows service running as SYSTEM. It is the only privileged part. Each app process runs as its own Windows service under a *virtual account* (`NT SERVICE\CaseFiles-api`, `-worker`, `-models`): no password, can't sign in, not an administrator, and only the file permissions the installer grants it. The service is hosted by `launcher.exe host <service>`, which runs the program in a job object (so nothing it starts can outlive it), restarts it after a crash, and writes its log with secrets and case-file paths removed. The app has no route, credential or network path that can reach the launcher's control API, and can't read the launcher's folder.

This replaces the Docker design's container boundary. The trade-off is covered in Flag 1.

**2.2 Language: Go (recommended).** A single signed `.exe` with no runtime to install, native Windows service and Service Control Manager support (`golang.org/x/sys/windows/svc`), and the Control Center's static files embedded in the binary. Python would need a bundled interpreter and a service wrapper; that is more to install and more to patch. Trade-off: the backend is Python, so the launcher is a second language in the repo. It is small (a few thousand lines) and rarely changes.

**2.3 Network binding.**
- Default: the Control Center listens on **127.0.0.1 only**, so it is reachable only from the host machine itself.
- Optional (Admin switch, audited): also listen on the LAN interface, restricted to private address ranges (10/8, 172.16/12, 192.168/16) and never 0.0.0.0 on a public interface.
- HTTPS in both cases, using the same local CA as the main app.

**Decided:** localhost-only by default, LAN as an audited opt-in (fred, 2026-09-29).

**2.4 Authentication.** The launcher cannot rely on the app's database, because the app may be stopped. So it keeps its own tiny credential store:
- The installer generates a **one-time setup code** and shows it at the end of install (also written to a file only the Windows user can read). Wizard step 1 asks for it. This closes the window where anyone on the machine could claim the Admin account.
- Wizard step 3 creates the Admin account once and provisions it in both the launcher and the app (the launcher stores its own Argon2id hash). Changing the Admin password in the app updates both.
- Sessions: HttpOnly, Secure, SameSite=Strict cookie, 8-hour idle timeout; CSRF token and Origin check on every state-changing request; login rate limit and lockout after repeated failures. Optional TOTP second factor.

**2.5 Allow-listed actions only.** The control API is a fixed list. Each action takes only values from a fixed enum; there is no free-text parameter anywhere.

| Action | Parameters |
|---|---|
| `service.start` / `service.stop` / `service.restart` | service ∈ {api, worker, models} |
| `stack.start_all` / `stack.stop_all` | none |
| `model.pull` | model ∈ curated catalogue (e.g. `bge-m3`, `multilingual-e5-large`) |
| `diagnostics.bundle` | none |
| `cert.renew` | none |
| `launcher.set_lan_binding` | on / off |

Each service maps to a fixed Windows service name (`CaseFiles-<id>`) and a fixed program, arguments and environment compiled into the launcher; nothing comes from the request. The launcher can only start, stop and query those three services through the Service Control Manager. There is no shell, no free-text path to a command line, and no way to register a new service from the browser. Tests assert that anything outside this table is rejected (unknown action, unknown service, extra or duplicate fields, trailing data), that each service's program and arguments are fixed, and that the environment passed to the app is filtered to a short allow-list.

*Revised 2026-09-29: Docker Compose replaced by per-service Windows services.*

**2.6 Audit.** Every launcher action, login, failed login and binding change is written to an append-only JSON Lines file with a hash chain (each entry includes the hash of the previous one, so edits are detectable). The app imports these into the Audit log page. Audit writes happen before the action runs; if the write fails the action is refused.

**2.6b Launcher files live outside the data folder.** The app services can write the data folder, so the launcher's credentials, audit log and CA key are kept in separate folders that only Administrators and SYSTEM can open. The web app can read its own server certificate and key (granted per file), never the CA key.

**2.7 Diagnostics bundle.** Contains service logs, versions, system checks and config with secrets removed. **It never includes case data, document text, file names, API keys or password hashes.** Log lines are passed through a redaction filter before they are stored, not only when exported.

**2.8 Service hardening.** Virtual accounts per service (2.1); file permissions set by the installer so each service reaches only its folders; the web app refuses clients outside loopback and private ranges even if the firewall rule is changed; Windows Firewall opens the web app port on Private networks only and blocks the worker from the internet; Ollama listens on 127.0.0.1 only. The launcher remembers which services the Admin started and starts them again at boot (no sign-in needed). A crashed service is restarted with a growing delay and the status screen says "Restarted automatically after a crash"; after five crashes in five minutes it is left stopped with an explanation.

**2.9 Non-admin status.** The "Indexing 40 of 200 files" banner comes from the app's own job queue, not from the launcher, so non-admin users never touch the control plane.

## 3. Vector store recommendation: LanceDB (embedded)

**Recommendation: LanceDB, embedded in the worker and API processes, one table per case stored at `cases/<case-id>/vectors/`. SQLite FTS5 stays the keyword index. Results are merged with reciprocal rank fusion, then re-ranked.**

Why, measured against your requirements:

| Requirement | LanceDB | sqlite-vec | Qdrant (local) | Chroma |
|---|---|---|---|---|
| No extra service to run | Yes, a library | Yes, a SQLite extension | No, its own server | Library or server |
| Per-case isolation | A table per case in the case's own folder; deleting or exporting a case takes its vectors with it | Partition column in one DB file | Collection per case, or payload filter | Collection per case |
| Sub-second at large scale (a full PST can reach millions of chunks) | ANN index (IVF-PQ), handles millions | Brute-force scan today; fine up to roughly 100k–200k chunks, slow beyond | ANN (HNSW), excellent | ANN, but a history of persistence and performance problems at scale |
| Metadata filters (folder, type, date, sender) | Yes, SQL-like, pre-filtered | Yes, via SQL | Yes, best in class | Yes |
| Maturity | Company-backed, widely used, pre-1.0 API | Single maintainer, pre-1.0 | Mature | Mature but churned |
| Backup | Copy the folder | Copy the DB file | Snapshot API | Copy the folder |

- **Why not sqlite-vec**, which is the tidiest (everything in one file, one transaction): its search is brute force, so the "under a second" target breaks once a case holds a large PST. I'd reconsider it when it ships a stable ANN index.
- **Why not Qdrant**: it is excellent, but it is one more service to start, monitor, secure and back up, and it keeps vectors in its own storage rather than inside each case folder. It is the right move if we ever need many concurrent writers.
- **Why not Chroma**: repeated breaking changes to its storage format and weaker behaviour at millions of rows.

**Consistency.** SQLite is the source of truth for documents and chunks. Vectors are derived data, rebuilt from the stored Markdown and chunk table at any time. Only the worker writes to LanceDB (the API only reads), which avoids LanceDB's multi-writer limits. A nightly check compares chunk counts and flags drift on the status screen.

**Control Center impact.** Because the vector store is a library, it appears as a **system check** ("Vector index: 12 cases, 184,302 chunks, model bge-m3 v1") rather than a start/stop service. The embedding model service (Ollama) is the service you'll see.

**Embeddings.** Default local model: **`bge-m3`** via Ollama (multilingual, 8k context, 1024 dimensions). On a CPU-only machine it manages roughly tens of chunks per second, so a few thousand files index in minutes to an hour; `multilingual-e5-small` is the fast fallback. The model name and version are stored per case table; changing either triggers the full re-index warning.

## 4. Flags

1. **Less isolation than containers for file parsing.** The worker opens untrusted files (PDF, PST, email attachments). In a container, a parser exploit was boxed in by a separate filesystem and network. Now it runs as `NT SERVICE\CaseFiles-worker`: it can read and change case data (it has to), can't read the launcher's credentials, audit log or CA key, can't reach the internet, and isn't an administrator. That is solid, but a flaw in a parser could still damage case data on disk. Mitigations: keep originals read-only after import (phase 3), keep Python packages and Tesseract current through the installer, and back up the data folder.
2. **The launcher downloads and installs programs.** At its first start (and whenever something is missing or out of date) the launcher, running as SYSTEM, fetches Python, Tesseract and Ollama from their official release pages, the Python packages from PyPI, and the embedding model from the Ollama registry. It then runs the Tesseract installer and pip. This is what lets the Control Center check and install everything from the browser. To keep it within the launcher's rules: the browser can only name a component from a compiled-in list (`component.install`, `components.install_missing`), never a URL, file or command. Every file is pinned by SHA-256 compiled into `launcher.exe` (`launcher/internal/components/runtimes.json`, plus `requirements-windows.txt` with `--require-hashes`), and CI checks the pins. Downloads are kept in the launcher's own folder, which the app services can't write to, so they can't swap an installer before it runs. Every install is written to the audit log. The model download has no checksum pin; it comes from the Ollama registry over HTTPS by digest. An offline bundle is possible later if the host has no internet.
3. **Data at rest is not encrypted by the app.** Originals, Markdown and vectors sit on disk as files. Turn on **BitLocker** for the drive holding the data folder; the installer will check and warn.
4. **Embeddings can leak content.** Choosing OpenAI or OpenRouter for embeddings sends every chunk of every document off the machine, not only the chunks behind one question. The confirmation gate will cover embedding runs as well as chat calls, with a stronger warning.
5. **Privilege and confidentiality with external AI providers.** Sending privileged material to a third party may raise waiver and confidentiality questions. The Settings page will show each provider's data-retention status and default to local.
6. **LAN address and certificate.** If the host's IP changes (DHCP), the certificate stops matching. Reserve the host's IP on your router or use a hostname such as `casefiles.local`; the installer will suggest this.
7. **Case isolation is enforced in the backend.** Every search, vector query and AI call derives the allowed case IDs from the signed-in user's permissions on the server and never trusts a case ID from the browser. Tests will prove that a user without access gets nothing, including from keyword search and chunk citations.

## 5. Other stack choices (for phase 2, not blocking)

Job queue: a table in SQLite (queue length, retry and failure lists for the Control Center), so there is no Redis to install or run. Frontend: React + TypeScript + Vite, Radix primitives, TanStack Virtual. The Control Center is built from the same frontend code and tokens and embedded in the launcher binary. PST: `libpff` via its official Python bindings (`libpff-python`, Windows wheels on PyPI), which reads multi-GB files without a Linux tool.

## 6. Railway readiness (decided 2026-09-29)

fred chose to keep the self-hosted plan but to keep a Railway deployment possible later. The build will follow these rules so that a move needs configuration, not a rewrite:

- **Configured by environment variables only.** The same Python code runs under the launcher on Windows and in a container on Railway (`services/api/Dockerfile` exists only for that). No Windows paths or host assumptions in the app. Each service reads `PORT`, `DATA_DIR` and a `DEPLOY_MODE=local|cloud` flag. Secrets come from the environment (Railway variables in the cloud, an encrypted local file at home).
- **Storage behind an interface.** Originals, Markdown and LanceDB tables go through one storage layer. Locally that is the data folder. On Railway it is a mounted volume. SQLite stays valid on a single Railway instance with a volume; the schema is kept Postgres-compatible (via SQLAlchemy and Alembic) in case the cloud setup ever needs Postgres.
- **The launcher is local-only.** In `cloud` mode the launcher and Control Center are not deployed; Railway's dashboard handles start, stop and restarts. The app's own status page (jobs, system checks, health) still works in both modes.
- **Health endpoints on every service** (`/health`, `/ready`) serve both the launcher and Railway's health checks.
- **The job queue lives in SQLite**, which works on a single Railway instance with a volume.
- **Embeddings in the cloud.** Running `bge-m3` on Railway is possible but costly on CPU. An external embedding provider would send every chunk off-site, so in `cloud` mode the app keeps the confirmation gate and shows a permanent "Hosted in the cloud" banner to every user.
- **Flag: moving to Railway changes the confidentiality model.** Case data would then be stored with Railway and its cloud provider. Before any real client data goes there, check client consent, where the data is stored, and Railway's data-processing terms.
