# Octop-inspired case workspace

Reference reviewed: [TencentCloud/Octop](https://github.com/TencentCloud/Octop), README, MIT licence and expert-team design at upstream HEAD 0c5a46ab5f82e5ad9d1a56fa09b00e542f042fda, plus the user's supplied screenshot.

This is an integration of the product pattern into Case File Manager, with an original implementation. It does not import Octop's general assistant runtime, code, branding or mascot.

| Octop pattern | Case File Manager |
|---|---|
| Shared knowledge base | Case-scoped original documents and extracted source passages |
| Specialist experts | Built-in and custom agents with search terms and review instructions |
| Parallel dispatch | Independent background jobs, up to three concurrent workers |
| Chat workspace | Questions over case evidence, with clickable citations |
| Tasks and history | Persistent review progress, findings, errors and exports |
| Tool approvals | Explicit per-action approval before external AI receives excerpts |

The screenshot guides the white workspace, rose accents, left navigation, welcoming assistant, quick starts and composer. The terminology and interactions serve legal evidence review.

## Review behaviour

Evidence search ranks text passages using topic terms. Chronology additionally recognises common date formats. It does not use embeddings or infer legal conclusions. Each task returns up to 18 candidate passages, which should be read in context.

AI analysis sends only the retrieved passages, their names and locations, and the user's question or agent instructions to the configured provider. Model output must use known passage identifiers. Unknown citations fail the task or question instead of being displayed. Accepted citations identify actual source text; users still need to verify a model's interpretation of that text.

Model management supports ChatGPT / OpenAI, Claude / Anthropic, DeepSeek and OpenRouter, plus a custom or local endpoint. Claude uses Anthropic's Messages API; the other adapters use Chat Completions. Only non-secret provider/model choices are persisted in SQLite. Keys are supplied to the server through secure environment settings. No key is sent to the browser or included in exports.

Tasks capture their provider and model when created. A later selection change affects new reviews only. Question and review requests also carry their expected provider/model, so an approval made before a selection changed cannot authorise sending to a different destination.

Separate agents run on a snapshot of the selected document passages. Every task retains its results and mode. Jobs interrupted by a server restart become failed with a recovery message. Approval is recorded locally and covers only that action; it is never remembered as permission for a future external request.

## Original project plan

The starting commit had only README.md and .gitignore. Its original plan is preserved in [original-project-plan.md](original-project-plan.md). This working workspace is an incremental foundation for that plan, not an implementation of the absent Windows service, multi-user authentication, OCR containers, PST conversion, hybrid search or hash-chained audit log.

Current deployment is local and single-user. Keep the server bound to loopback.
