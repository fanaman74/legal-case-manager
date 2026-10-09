# Verification of the document-and-agent workspace

Validated on the prepared cloud machine on 9 October 2026.

- The production TypeScript check and Vite build passed.
- All 21 API tests passed, exercising upload/extraction, selectable PDF text, OCR-needed rejection, DOCX/Excel/email/CSV, size/format validation, case isolation, agent editing, parallel review jobs, exact source citations, chat history, exports, interrupted jobs, and cross-site write rejection.
- All four provider protocols passed local HTTP fixture tests. Tests also reject stale approvals, preserve the model for running jobs, reject unknown citations and malformed review schemas, and keep valid empty AI analysis distinct from missing search matches.
- The real Chromium browser workflow passed upload, four agents, exact cited source viewing, questions, custom-agent create/edit, exports, model selection, desktop/mobile layouts, and external approve/cancel behaviour. Test data was isolated from the real workspace and explicitly marked synthetic.
- The saved setup script was exercised with npm ci, locked Python dependencies and a fresh build. The start script was exercised and restarted after changes. The service responds with database-backed health, provider metadata, a case list, and the built page.
- The before-change backup SHA-256 was rechecked successfully. Original checkout files and Git metadata were verified against the archive before any edits.

The service's actual case store remains empty: browser test cases and documents live only in a disposable test directory. No case material was sent to an external service during verification. Live paid-provider execution remains untested until an API key is securely configured. This is a local single-user release; OCR, PST conversion, the Windows launcher and multi-user authentication remain outside the current implementation.

The test suite emits a Starlette test-client deprecation notice about its httpx compatibility path. It does not affect the passing test outcomes or the application's provider clients.
