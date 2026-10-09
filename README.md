# Case File Manager

A local workspace for legal case documents and topic-focused agents, with an interface inspired by [TencentCloud/Octop](https://github.com/TencentCloud/Octop).

Upload documents, run specialists against the same evidence, and open every finding at its source. Evidence search works immediately. Optional AI analysis supports **Claude, ChatGPT / OpenAI, DeepSeek and OpenRouter**, with explicit approval before excerpts are sent externally.

## What works

- Separate case workspaces with persistent documents, conversations and review history.
- Bulk upload of PDF, DOCX, TXT, Markdown, CSV, EML and XLSX; preserved originals and extracted passages.
- Four built-in agents: Chronology, Financial evidence, Correspondence, and Duties & agreements.
- Create and edit agents with custom topics, search terms and instructions; run several independently against all documents or a selection.
- Cited questions, source viewer, original-file downloads and JSON review exports.
- Model management with provider selection and editable model identifiers, persisted locally.
- Provider-specific Claude requests and OpenAI-compatible requests for the other providers.
- Per-action external approval, source identifier validation, visible failures and interrupted-job recovery.

This is a **local, single-user release**. Keep it bound to loopback. The original Windows launcher, multi-user authentication, OCR, PST parsing, embeddings and hybrid search remain future work; the documentation-only starting plan is preserved in [docs/original-project-plan.md](docs/original-project-plan.md).

## Run

Requires Python 3.12+ and Node 20.19+, 22.12+ or 24+.

From the repository root:

    bash scripts/setup.sh
    bash scripts/start.sh

The workspace and API run together on loopback port 8000. The start script binds to 127.0.0.1. CASEFILES_PORT selects another port. The first run has no fabricated case data: create a case and upload your documents.

For frontend development, keep the API running and, in another shell:

    cd control-center
    npm --cache ../.cache/npm run dev

Vite proxies /api to the local service. Setup uses locked Python dependencies and npm ci. Generated outputs and local data are ignored by Git.

## Models and API keys

Open **Models** in the sidebar, choose a provider, select or enter a model identifier, then choose **Use this model**. A selection does not contact the provider or send documents. Model suggestions are editable and availability depends on the account.

Configure whichever provider keys you want through secure server environment settings, then restart:

| Provider | Server variable | Destination |
|---|---|---|
| ChatGPT / OpenAI | CASEFILES_OPENAI_API_KEY | api.openai.com |
| Claude / Anthropic | CASEFILES_ANTHROPIC_API_KEY | api.anthropic.com |
| DeepSeek | CASEFILES_DEEPSEEK_API_KEY | api.deepseek.com |
| OpenRouter | CASEFILES_OPENROUTER_API_KEY | openrouter.ai |

Keys stay on the server: they are never returned to the browser or case exports. Configure one or several providers; only the selected one is used. A consumer ChatGPT or Claude subscription is separate from API access and billing.

A custom OpenAI-compatible endpoint is also supported through CASEFILES_AI_BASE_URL, CASEFILES_AI_MODEL and CASEFILES_AI_API_KEY. A loopback endpoint, such as a local model's /v1 API, may use HTTP without a key. External endpoints require HTTPS and a key. The custom provider appears in Model management when its base URL is configured.

Enable **AI analysis** for the question or review you want to run. External requests show a confirmation naming the provider, model and documents in scope. Only instructions/questions, file names and up to 18 retrieved text passages per request are sent; original files are not sent. Each agent makes a separate request. Approval is recorded locally, applies to that action only, and provider retention rules still apply. A provider/model change invalidates a stale approval. Running tasks retain their starting provider and model.

## Documents and storage

The upload limit is 25 MB per file, 1,000 pages per PDF and two million extracted characters per document. Scanned PDFs need OCR first. Convert legacy DOC and Outlook PST files before uploading. DOCX locations identify extracted sections, not printed page numbers; XLSX sections identify sheets.

The default data/ directory contains casefiles.sqlite3 and the documents/ originals. CASEFILES_DATA_DIR can select another local directory. Stop the service before copying the whole data directory for a consistent backup. Review exports include cited text and results but do not include all original files.

Evidence search uses topic terms and date detection, not a language model. AI interprets retrieved candidates; a linked passage does not guarantee a model's interpretation is correct. No-match results are never treated as proof that evidence is absent. See [docs/octop-adaptation.md](docs/octop-adaptation.md) for architecture and scope.

## Validation

    .venv/bin/python -m pytest services/api/tests -q
    bash scripts/build.sh

Optional real-browser verification uses a separate, disposable data store. Install services/api/requirements-ui.txt into a Python environment and supply Chromium through CASEFILES_CHROMIUM (the cloud machine has /usr/bin/chromium), or install a Playwright browser.

    CASEFILES_DATA_DIR=/tmp/casefiles-ui-test CASEFILES_PORT=8001 bash scripts/start.sh
    # In another shell using a Python environment with Playwright:
    CASEFILES_UI_TEST_URL=http://127.0.0.1:8001 python scripts/test-ui.py

Do not point the browser test at a real case workspace. It creates test cases and changes the selected model. Provider adapters and consent are tested with local HTTP fixtures; live paid API calls need configured keys and are not part of the default tests.

## Backup and rollback

Before these changes, the entire original checkout and Git history were archived and verified. See [docs/rollback.md](docs/rollback.md) for the archive location and recovery commands. Recovering into a separate directory preserves any later work and documents.

## Repository layout

- control-center/: React, TypeScript and Vite workspace.
- services/api/: FastAPI, SQLite storage, extraction, review workers and model adapters.
- scripts/: reproducible setup, build, startup and browser validation.
- docs/: original plan, Octop adaptation and rollback instructions.
