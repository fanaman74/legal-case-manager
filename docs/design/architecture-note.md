# Architecture note: launcher security and vector store

Status: **draft for fred's review** (2026-09-29). Companion to [design-brief.md](design-brief.md).
Items marked **Decision** need your answer; items marked **Flag** are risks you asked me to call out.

---

## 1. Shape of the system

```
Windows host
├── Launcher  (Windows service, starts at boot, runs on the HOST, not in Docker)
│     ├── serves the Control Center + its own auth
│     ├── talks to Docker Engine over the local named pipe (\\.\pipe\docker_engine)
│     └── polls each service's /health endpoint
└── Docker Compose project "casefiles"
      ├── api        FastAPI + built frontend        (non-root, no Docker socket)
      ├── worker     conversion, chunking, embedding (non-root, no Docker socket)
      ├── queue      Redis for RQ (internal network only)
      ├── ocr        ocrmypdf/Tesseract               (internal only)
      ├── pst        readpst/libpff parser            (internal only)
      └── ollama     local embedding model (+ optional local chat model; internal only)
C:\CaseFiles\launcher  → launcher credentials, audit log, settings (never mounted)
C:\CaseFiles\certs     → local CA + server cert (only server.crt/key mounted, read-only)
C:\CaseFiles\data      → mounted into api/worker only
      ├── app.db           SQLite: users, cases, files, chunks, FTS5, audit log
      └── cases/<case-id>/original | markdown | vectors
```

Only `api` publishes a port to the LAN (HTTPS). Every other container sits on an internal Docker network with no published port.

## 2. Launcher security model

A web page that can start and stop services is a privileged entry point, so the launcher is built to do very little, very carefully.

**2.1 Where it runs.** The launcher runs as a native Windows service on the host, outside Docker. If it ran inside a container it would need the Docker socket mounted, and any container with the socket is effectively administrator on the machine. Keeping it on the host means **no container ever gets the Docker socket**, including the main app. The app has no route, credential or network path that can reach the launcher's control API.

**2.2 Language: Go (recommended).** A single signed `.exe` with no runtime to install, native Windows service support (`golang.org/x/sys/windows/svc`), an official Docker API client, and the Control Center's static files embedded in the binary. Python would need a bundled interpreter and a service wrapper; that is more to install and more to patch. Trade-off: the backend is Python, so the launcher is a second language in the repo. It is small (a few thousand lines) and rarely changes.

**2.3 Network binding.**
- Default: the Control Center listens on **127.0.0.1 only**, so it is reachable only from the host machine itself.
- Optional (Admin switch, audited): also listen on the LAN interface, restricted to private address ranges (10/8, 172.16/12, 192.168/16) and never 0.0.0.0 on a public interface.
- HTTPS in both cases, using the same local CA as the main app.

**Decision 1:** your spec says "bind to the LAN only". I recommend localhost-only by default with LAN as an opt-in, because the control plane is the most powerful thing on the machine and most days you'll be at the host anyway. Say if you'd rather have LAN on from day one.

**2.4 Authentication.** The launcher cannot rely on the app's database, because the app may be stopped. So it keeps its own tiny credential store:
- The installer generates a **one-time setup code** and shows it at the end of install (also written to a file only the Windows user can read). Wizard step 1 asks for it. This closes the window where anyone on the machine could claim the Admin account.
- Wizard step 3 creates the Admin account once and provisions it in both the launcher and the app (the launcher stores its own Argon2id hash). Changing the Admin password in the app updates both.
- Sessions: HttpOnly, Secure, SameSite=Strict cookie, 8-hour idle timeout; CSRF token and Origin check on every state-changing request; login rate limit and lockout after repeated failures. Optional TOTP second factor.

**2.5 Allow-listed actions only.** The control API is a fixed list. Each action takes only values from a fixed enum; there is no free-text parameter anywhere.

| Action | Parameters |
|---|---|
| `service.start` / `service.stop` / `service.restart` | service ∈ {api, worker, queue, ocr, pst, ollama} |
| `stack.start_all` / `stack.stop_all` | none |
| `model.pull` | model ∈ curated catalogue (e.g. `bge-m3`, `multilingual-e5-large`) |
| `diagnostics.bundle` | none |
| `cert.renew` | none |
| `launcher.set_lan_binding` | on / off |

State changes run `docker compose` (up, stop) directly, without a shell, with an argument list built only from the launcher's own config (compose file path, project name) and service keys from the compiled-in catalog. Status, stats and logs use a read-only Docker API client that only sees containers labelled `com.docker.compose.project=casefiles`. The launcher never calls `exec`, never creates containers from images outside compose.yaml, and has no free-text path to the command line. Tests assert that anything outside this table is rejected (unknown action, unknown service, extra or duplicate fields, trailing data) and that every compose argument is a catalog value.

*Implemented in phase 1 (2026-09-29): this replaces the earlier draft's "no shell-out" wording, because compose is needed to create containers on first start.*

**2.6 Audit.** Every launcher action, login, failed login and binding change is written to an append-only JSON Lines file with a hash chain (each entry includes the hash of the previous one, so edits are detectable). The app imports these into the Audit log page. Audit writes happen before the action runs; if the write fails the action is refused.

**2.6b Launcher files live outside the data folder.** The data folder is mounted into the app containers, so the launcher's credentials, audit log and CA key are kept in separate folders that no container can see.

**2.7 Diagnostics bundle.** Contains service logs, versions, system checks and config with secrets removed. **It never includes case data, document text, file names, API keys or password hashes.** Log lines are passed through a redaction filter before they are stored, not only when exported.

**2.8 Container hardening.** All app containers run as non-root, with read-only root filesystems where possible, `no-new-privileges`, dropped Linux capabilities, and `restart: unless-stopped`. The launcher also reconciles desired state (if you pressed Start all, it restarts anything that dies and records "Restarted automatically after a crash" for the status screen).

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

1. **Docker Desktop only starts after a Windows sign-in.** The launcher service runs at boot, but Docker Desktop does not start until a user signs in. For services that come back after a reboot with nobody signed in, the options are Docker Engine inside WSL2 started by a scheduled task, or auto sign-in on the host. I'll build for "starts when you sign in" first and document the headless option. **Decision 2:** is the host machine usually signed in, or do you need unattended reboots?
2. **Docker Desktop licensing.** Free for personal use and small businesses (under 250 staff and under $10M revenue). Worth confirming for your firm.
3. **Data at rest is not encrypted by the app.** Originals, Markdown and vectors sit on disk as files. Turn on **BitLocker** for the drive holding the data folder; the installer will check and warn.
4. **Embeddings can leak content.** Choosing OpenAI or OpenRouter for embeddings sends every chunk of every document off the machine, not only the chunks behind one question. The confirmation gate will cover embedding runs as well as chat calls, with a stronger warning.
5. **Privilege and confidentiality with external AI providers.** Sending privileged material to a third party may raise waiver and confidentiality questions. The Settings page will show each provider's data-retention status and default to local.
6. **LAN address and certificate.** If the host's IP changes (DHCP), the certificate stops matching. Reserve the host's IP on your router or use a hostname such as `casefiles.local`; the installer will suggest this.
7. **Case isolation is enforced in the backend.** Every search, vector query and AI call derives the allowed case IDs from the signed-in user's permissions on the server and never trusts a case ID from the browser. Tests will prove that a user without access gets nothing, including from keyword search and chunk citations.

## 5. Other stack choices (for phase 2, not blocking)

Job queue: RQ on Redis (reliable queue length, retry and failure lists for the Control Center). Frontend: React + TypeScript + Vite, Radix primitives, TanStack Virtual. The Control Center is built from the same frontend code and tokens and embedded in the launcher binary. PST: `readpst` first (mature, streams multi-GB files), `pypff` as fallback.

## 6. Railway readiness (decided 2026-09-29)

fred chose to keep the self-hosted plan but to keep a Railway deployment possible later. The build will follow these rules so that a move needs configuration, not a rewrite:

- **One image per service, configured by environment variables only.** No Windows paths or host assumptions inside containers. Each service reads `PORT`, `DATA_DIR` and a `DEPLOY_MODE=local|cloud` flag. Secrets come from the environment (Railway variables in the cloud, an encrypted local file at home).
- **Storage behind an interface.** Originals, Markdown and LanceDB tables go through one storage layer. Locally that is the data folder. On Railway it is a mounted volume. SQLite stays valid on a single Railway instance with a volume; the schema is kept Postgres-compatible (via SQLAlchemy and Alembic) in case the cloud setup ever needs Postgres.
- **The launcher is local-only.** In `cloud` mode the launcher and Control Center are not deployed; Railway's dashboard handles start, stop and restarts. The app's own status page (jobs, system checks, health) still works in both modes.
- **Health endpoints on every service** (`/health`, `/ready`) serve both the launcher and Railway's health checks.
- **The job queue uses Redis**, which Railway offers as a managed add-on, so no code changes there.
- **Embeddings in the cloud.** Running `bge-m3` on Railway is possible but costly on CPU. An external embedding provider would send every chunk off-site, so in `cloud` mode the app keeps the confirmation gate and shows a permanent "Hosted in the cloud" banner to every user.
- **Flag: moving to Railway changes the confidentiality model.** Case data would then be stored with Railway and its cloud provider. Before any real client data goes there, check client consent, where the data is stored, and Railway's data-processing terms.
