# Phases 3 to 7 screen plan: conversion, search, PST, AI, export

Status: **draft for fred's approval** (2026-10-11). Builds on the approved [design brief](design-brief.md), [tokens](tokens.css) and the phase 2 screens. The backend is being built meanwhile; if there is no answer, the screens follow this plan.
Design skill: neither Impeccable nor frontend-design is installed, so this applies the brief, as in phases 1 and 2.

Everything here comes from the original spec and the brief's page inventory. This plan only says how each screen behaves and which defaults I picked.

---

## Delivery

One pull request per phase, each built on the one before, so each can be reviewed and tried on its own:

| Phase | What you get |
|---|---|
| 3 | Conversion of DOCX, PDF (with OCR for scans), MSG, EML, TXT and images to Markdown; background jobs with retry and re-convert; email attachments extracted and linked; document viewer |
| 4 | Chunking, local embeddings (bge-m3), LanceDB vector index, hybrid search with filters |
| 5 | PST upload, indexing, keyword search with operators, email preview, Add to case |
| 6 | Settings (chat providers, embedding model), confirmation gate, Ask (cited answers), Review (summaries, chronology, issue relevance, extraction, tag suggestions) |
| 7 | Case export as ZIP, near-duplicate detection, backup guide, final design review |

## Defaults I picked (say if you want any changed)

1. **A PST belongs to one case.** It is uploaded on that case's Email tab, only that case's members can search it, and Add to case files emails into that case. This keeps the case boundary strict (nobody searches a mailbox for a case they aren't on).
2. **Embeddings stay on this computer** (bge-m3 through the local models service). External embedding providers (OpenAI, OpenRouter) are not offered: they would send every chunk of every document off the machine, and the worker is blocked from the internet by design. Chat providers can be external, behind the confirmation gate.
3. **Semantic search over a whole PST is not built** (the spec marks it optional and off by default). Emails become semantically searchable once added to a case.
4. **AI review runs when someone asks for it**, not automatically on upload, so nothing is ever sent to a provider without the confirmation step. Duplicate and near-duplicate flags are automatic (no AI involved).
5. **Local chat model:** an optional download in the Control Center (Components), not installed by default because it is several GB.

## Who can do what (added to the phase 2 table)

| | Admin | Editor | Read-only |
|---|---|---|---|
| Search, read Markdown, ask cited questions, run review | Yes | Assigned cases | Assigned cases |
| Re-convert, OCR a scanned PDF, retry failed files | Yes | Assigned cases | No |
| Upload and search PSTs, add emails to the case | Yes | Assigned cases | Search only |
| Export a case as ZIP | Yes | No | No |
| Settings (providers, models) | Yes | No | No |

Every search, vector query and AI request is limited on the server to cases the person is assigned to.

## Screens

**Case tabs** become **Files · Search · Ask · Review · Email · Details · People · Activity**.

**1. Files (updated).** Status column now shows the conversion state with icon and label: Queued, Converting, Converted, Converted with warnings, Scanned (offers OCR), Failed (reason + Retry), Duplicate, Near-duplicate. A fixed-height progress line sits above the table while work runs: "Converting 12 of 80 files · Indexing 40 of 200". The **What to do next** panel walks through: upload → wait for conversion → review N warnings / N failures → search or ask. It collapses to one line once everything is indexed. Email attachments appear indented under their email, with a paperclip.

**2. Document viewer.** Opens from a file row as a full-width split view (the drawer stays for quick details). Left: the original (PDF and scanned pages as page images; emails as a header block and body; images as the image; Word documents show a note that the original opens in Word, with Download). Right: the converted Markdown in reading type (Source Serif, 68ch), with page markers shown as quiet "Page 12" rules. Scrolling one side keeps the page in step on the other. Top bar: file name, page count, OCR badge, warnings, SHA-256, Download original, Re-convert, Run OCR (scanned PDFs). A Front matter toggle shows the YAML header. Keyboard: J/K next/previous page, Esc closes.

**3. Search.** One search box with filters beside it: folder, type, date range, sender. Results list: file name and folder, page, heading path, the passage with matched words highlighted, and a "Keyword" / "Meaning" / "Both" tag saying why it matched. Clicking a result opens the viewer at that page with the passage highlighted. States: index still building ("Results so far cover 120 of 200 files"), no results (with tips), nothing indexed yet.

**4. Ask.** Question box at the bottom, conversation above. Each answer sits in the AI-generated block (dashed teal outline, "AI-generated, unverified"), with numbered citations that open the passage. Under the answer: the sources used (file · page). If the sources don't answer the question, it says so plainly. Provider badge shows "On this computer" or "Leaves this machine: Anthropic".

**5. Confirmation gate (dialog).** Before any call to an external provider: provider and model, what will be sent (N passages from M files, with the file list expandable to each passage), a red "Sends data to Anthropic" line, and **Send** / **Cancel**. There is no "don't ask again". Every call is in the audit log.

**6. Review.** Pick a task and a scope (a file, a folder, or the whole case): **Summarise**, **Chronology**, **Check relevance to an issue** (you type the issue), **Extract parties, dates and amounts**, **Suggest tags and document type**. Results appear as AI-generated blocks with citations and are saved in the case's review history. Tag suggestions have Accept per tag; nothing is applied without a click.

**7. Email (PSTs).** Upload a PST (drop zone; size limit 50 GB). Each PST shows indexing progress ("Indexing 41,200 of 96,000 emails"). Search box understands "exact phrases", AND, OR, NOT, with filters for date range, from, to, folder and has-attachment. Results: virtualised list (from, subject, date, folder, attachment clip, highlighted snippet), multi-select with checkboxes and Shift-click. Right pane: full email preview. **Add to case** (with "Include attachments" checked by default) converts the selected emails and runs them through conversion and indexing. Emails already added show "In case" and can't be added twice.

**8. Settings (Admin, new page in the left rail).** **Chat providers:** Claude (Anthropic), OpenAI, DeepSeek, OpenRouter, Ollama (local), LM Studio (local). Each has API key (write-only: shows "Saved, ends in …4f2a" after saving), base URL, default model, Test connection, and a badge: "On this computer" or "Sends data off this machine" in the warning style. **Default provider** for Ask and for Review. **Embedding model:** the local model in use, with chunk count, and "Re-index all cases" behind a warning ("Every case will be re-indexed; search is partial until it finishes").

**9. Jobs (status).** Non-Admins see a status banner in the top bar ("Indexing 40 of 200 files"). The Control Center gets a **Background jobs** section: queue length, running jobs with progress, recent failures per file, and the vector index check ("12 cases, 184,302 passages, bge-m3").

**10. Export (Admin, case menu).** "Export case as ZIP" builds originals, Markdown, metadata (JSON) and the case's audit trail in the background, then offers the download. The export is audited.

## States every screen covers

Empty with the next step, loading at final size, in progress with counts, partial failure listed per file with Retry, error (what, why, next step), permission denied, both themes, checked at 1440px and 1024px.

## Decisions for you

1. Approve the plan, or tell me what to change.
2. Any of the five defaults above you want the other way.
