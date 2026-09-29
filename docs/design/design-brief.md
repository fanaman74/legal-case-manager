# Design brief: Legal Case File Manager

Status: **draft for fred's approval** (2026-09-29). No UI code is written until this is approved.
Tokens: [tokens.css](tokens.css) (source of truth) and [tokens.json](tokens.json) (same values, for tooling).
Design skill: neither Impeccable nor frontend-design is installed in this project, so this brief applies general design practice. Add Impeccable and the phase reviews will run against it.

---

## 1. Users and their tasks

| User | Who | Main tasks | Sees |
|---|---|---|---|
| **Admin** | fred, at the host machine or a LAN laptop | Start/stop services, run first-run setup, manage users and case access, configure providers, read the audit log, plus everything an Editor does | Control Center, Settings, Users, Audit log, all assigned cases |
| **Editor** | Associates, paralegals | Upload folders and PSTs, review conversions and warnings, search, ask cited questions, build chronologies, tag, add emails to a case | Assigned cases, PST library, their own jobs |
| **Read-only** | Co-counsel, reviewers, possibly clients | Browse, read, search and ask within assigned cases; export if permitted | Assigned cases only; no upload, no delete |

Working conditions that shape the design: long sessions of reading, many documents open over a day, work split between reading carefully and scanning lists of thousands of items, and high stakes when something is wrong (a missing page, a wrong citation, data sent off the machine).

## 2. Tone

**Calm, precise, trustworthy. A well-kept case file, not a dashboard.**

- Paper-and-ink neutrals with a slight warm bias, one deep ink-blue accent, no gradients, no glass, no illustrations.
- Structure comes from typography, alignment and thin rules, not from cards and shadows. Shadows are reserved for things that float (menus, dialogs).
- Precision is shown with real legal and forensic detail: page markers, SHA-256 hashes in mono, Bates-style references, exact counts ("Indexing 40 of 200 files"), never vague spinners.
- Copy is plain-language and imperative: what happened, why, what to do next. No apologies, no jargon in user-facing errors.

## 3. Typography

All fonts are **bundled locally** (no Google Fonts or CDN requests from the app: this is a no-telemetry, confidential tool).

| Role | Face | Use |
|---|---|---|
| UI | **IBM Plex Sans** (400/500/600) | Navigation, tables, forms, labels. Good tabular figures, clear at 13–14px. |
| Reading | **Source Serif 4** (400/600, optical sizes) | Markdown and email bodies in the document viewer, AI answers. Built for long reading. |
| Data | **IBM Plex Mono** (400/500) | Hashes, doc IDs, paths, page markers, log lines. |

Type scale (UI, rem at 16px root): 12 · 13 · **14 (base)** · 16 · 18 · 22 · 28. The UI base is 14px because the app is dense; the reading base is **17px at 1.6 line height with a 68ch measure**. No display sizes above 28px anywhere; page titles are 22px.

## 4. Design tokens (summary)

Full values in `tokens.css`. Contrast was checked against WCAG 2.2 AA (text ≥ 4.5:1, UI and focus ≥ 3:1) in both themes.

**Color, light theme**

| Token | Value | Role |
|---|---|---|
| `--paper` | `#F6F5F1` | App background |
| `--surface` | `#FFFFFF` | Panels, table body, document page |
| `--sunken` | `#EDEBE5` | Sidebars, table headers, code |
| `--rule` | `#D9D5CC` | Borders and dividers |
| `--ink` | `#1D1C1A` | Primary text |
| `--ink-muted` | `#5B574F` | Secondary text (6.6:1 on paper) |
| `--accent` | `#2A4580` | Links, primary buttons, selection, focus (9.3:1 on white) |

Dark theme uses the same roles: paper `#131311`, surface `#1B1A18`, sunken `#232220`, rule `#36342F`, ink `#ECE9E2`, muted `#A7A195`, accent `#94AEE6`.

**State language.** Every state has a colour, an icon shape and a text label, so it never relies on colour alone.

| State | Colour (light) | Icon shape | Label |
|---|---|---|---|
| Converted / Running / Done | green `#2D6A3A` | check in circle (filled dot for services) | "Converted", "Running" |
| In progress / Starting | accent `#2A4580` | half-filled ring; progress bar with count | "Converting 12 of 80", "Starting" |
| Warning | amber `#8A5700` | triangle | "3 warnings" |
| Failed / Error | red `#A3262A` | octagon with × | "Failed: password-protected PDF" |
| Stopped / Not started | muted ink | hollow square | "Stopped" |
| Duplicate | violet `#5E4A9A` | two overlapping squares | "Duplicate of …" |
| Added to case | accent | bookmark with tick | "In case: Smith v Jones" |
| **AI-generated** | teal `#0F6570` | **dashed outline** around the whole block + "AI-generated, unverified" label | Always shown, cannot be dismissed |
| Leaves this machine | red text on amber tint | outward arrow | "Sends data to OpenAI" |
| Permission denied | muted ink | lock | "You don't have access to this case" |

**Spacing** on a 4px grid: 2 · 4 · 8 · 12 · 16 · 24 · 32 · 48. Table rows are 32px (compact, default) or 40px (comfortable, user setting).

**Radius**: 2px (inputs, chips), 4px (buttons, panels), 8px (dialogs). Small radii keep the document feel.

**Elevation**: level 0 is flat with a rule; level 1 (menus, popovers) and level 2 (dialogs, confirmation gate) are the only shadows.

**Motion**: 120ms for hover and focus, 200ms for panels, ease-out. Under `prefers-reduced-motion`, all transitions go to 0 and progress spinners become static progress text. Progress updates never change element size (fixed-width counters, reserved space) so nothing jumps.

**Focus**: 2px accent outline with 2px offset on every interactive element, always visible on keyboard focus.

## 5. Navigation

Two separate apps that share the tokens:

1. **Control Center** (served by the launcher, its own address, Admin only). Single page with sections: Services · Jobs · System checks · Logs · Setup wizard. Always reachable even when the main app is stopped. Links across to the main app once it is running.
2. **Main app.** Left rail with: **Cases** · **PST library** · **Jobs** · and, for Admin only, a separated group **Users** · **Settings** · **Audit log** · **Control Center ↗**. A top bar holds the case switcher, global search within the current case, the status banner ("Indexing 40 of 200 files"), and the user menu.

Inside a case, tabs across the top: **Files** · **Search** · **Ask** · **Review** · **Activity**. The "What to do next" panel sits on the right of the Files tab and collapses to a single line once the case is fully indexed.

Every page has a stable URL (deep-linkable to a case, a file, a page within a file, and a search).

## 6. Page inventory

| Page | Purpose | Notable states |
|---|---|---|
| Control Center: Services | Per-service state, uptime, version, CPU/mem, last health check, start/stop/restart, Start all / Stop all | Starting (live), crash-restarted (shows "Restarted automatically at 09:14 after a crash"), error with fix button |
| Control Center: Jobs | Queue length, running jobs with progress, per-file failures, retry | Empty queue, partial failure |
| Control Center: System checks | Disk, Docker, embedding model, OCR, certificate expiry, LAN address with copy and QR | Each check: pass / warn / fail with the next step |
| Control Center: Logs | Per-service log viewer, level and text filter, download diagnostics | Virtualized, follows tail |
| Setup wizard | 7 gated steps (prerequisites → services → Admin → embeddings → chat provider → first case → invite users) | Each step: to do, in progress, done, failed; cannot advance until the check passes |
| Sign in | Username and password | Locked out, wrong password, services stopped |
| Case list | All assigned cases, search, sort, archived filter, create case | Empty ("No cases yet. Create your first case"), no access |
| Case: Files | Folder tree, virtualized file list, document viewer, What to do next | Drop-zone empty state, uploading, converting, partial failure, duplicates |
| Document viewer | Original (PDF/email render) and converted Markdown side by side, page-synced, front matter drawer, hash | Scanned PDF offered for OCR, conversion warnings inline |
| Case: Search | Hybrid search with filters (folder, type, date, sender), highlighted passages, hit → exact page | No results, index still building ("Results so far cover 120 of 200 files") |
| Case: Ask | Cited Q&A over this case only; each claim links to file and page | Not in sources ("The documents in this case don't answer this"), confirmation gate for external providers |
| Case: Review | Summaries, chronology, issue relevance, extracted parties/dates/amounts, tag suggestions | All output in the AI-generated treatment |
| Case: Activity | This case's audit trail | |
| PST library | Uploaded PSTs, indexing progress, keyword search with operators and filters, email preview, multi-select, Add to case | Multi-GB indexing, already-added marking |
| Jobs | The user's own jobs (Admin sees all) | |
| Settings | Chat providers, embedding provider (separate), per-task models, test connection | "Sends data off this machine" warning on each external provider; keys never shown after save |
| Users and permissions | Users, roles, case assignments, invite | |
| Audit log | Filterable by user, action, case, date; export | |
| Confirmation gate (dialog) | Lists exactly which files/chunks go to which provider and model before any external call | Cannot be skipped; "Remember for this session" is not offered |

## 7. Component list

Built on **Radix UI primitives** (unstyled, accessible) styled only with our tokens, so the app never looks like an off-the-shelf kit.

- **Layout**: AppShell, LeftRail, TopBar, SplitPane (resizable, keyboard-resizable), Drawer, Tabs
- **Data**: VirtualTable (TanStack Virtual; sortable, filterable, multi-select with shift/ctrl), VirtualTree (folder tree), KeyValueList, LogView, Chronology
- **State**: StatusBadge (colour + icon + label), ServiceRow, ProgressBar (fixed-width count), JobRow, SystemCheckRow, StatusBanner, EmptyState (always with a next-step action), ErrorPanel (what happened / why / next step / fix button), PermissionDenied
- **Documents**: DocumentViewer, MarkdownReader (reading typography), EmailView (header block, collapsed quoted replies, attachments), PageMarker, HashValue (mono, copy, truncated middle), Citation (file · page, opens the passage)
- **AI**: AIBlock (dashed outline + label), CitedAnswer, ConfirmationGate dialog, ProviderBadge ("Local" vs "Leaves this machine")
- **Input**: Button (primary, secondary, quiet, danger), IconButton, TextField, Select, Combobox, Checkbox, Switch, DateRange, SearchField with operator hints, Dropzone and FolderPicker, CopyButton, QRCode
- **Wizard**: WizardStepper, WizardStep (status + one action + check result)
- **Feedback**: Toast (for completed actions only, never for errors that need action), Dialog, Tooltip

## 8. States every screen designs for

Empty (with the next step), loading (skeleton at final size, no jump), in progress (counts, not spinners), partial failure (the batch continues, failures listed per file with retry), error (what, why, next step), success, and permission denied. Each key screen will be mocked in all of these before it is called done.

## 9. Accessibility and performance commitments

WCAG 2.2 AA contrast in both themes; full keyboard operation including tree, table, split panes and wizard; visible focus; ARIA labels and live regions for progress (polite, throttled so screen readers are not flooded); reduced motion honoured; light and dark themes follow the OS with a manual override. Desktop-first at 1280px+, usable at tablet width (1024px) by collapsing the tree and viewer into tabs. Lists of thousands of rows are virtualized.

## 10. What I need from you

Approve, or tell me what to change. The most useful things to react to: the ink-blue accent, the serif for reading, the 14px dense base size, and the dashed-outline treatment for AI-generated content.
