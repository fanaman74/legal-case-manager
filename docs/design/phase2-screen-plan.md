# Phase 2 screen plan: sign-in, users, cases, upload

Status: **draft for fred's approval** (2026-10-10). Builds on the approved [design brief](design-brief.md) and [tokens](tokens.css). No main-app UI code is written until this is approved; the backend (accounts, roles, cases, upload) is being built in the meantime.
Design skill: neither Impeccable nor frontend-design is installed, so this applies the brief and general practice, as in phase 1.

The main app is a new React + TypeScript + Vite app (`web/`) using Radix primitives styled with the shared tokens. The API serves it at `/`, replacing today's "Case File Manager is running" placeholder.

---

## Who can do what (enforced by the API, not just hidden in the UI)

| | Admin | Editor | Read-only |
|---|---|---|---|
| See cases | All | Assigned only | Assigned only |
| Create, rename, archive, delete cases; assign people | Yes | No | No |
| Edit case details and notes | Yes | Assigned cases | No |
| Upload files and folders, tag files | Yes | Assigned cases | No |
| View and download files | Yes | Assigned cases | Assigned cases |
| Users, Audit log | Yes | No | No |

A case someone can't see behaves as if it doesn't exist (404), so case names never leak.

**Default picked:** only the Admin creates cases. Say if Editors should be able to create cases too.

## Screens

**1. Sign in.** Centred, narrow, the same look as the Control Center sign-in. Username, password, Sign in. States: wrong details ("That username and password don't match"), locked out after 5 tries in 15 minutes ("Too many attempts. Try again in 15 minutes, or ask the Admin to unlock your account"), app stopped (handled by the browser; the Control Center explains). First sign-in with a temporary password goes straight to **Choose your password** (12+ characters).

The Admin signs in with the account created in the setup wizard. The launcher hands the Admin's password hash to the app, so there is still only one Admin password, changed in the Control Center.

**2. App shell.** Left rail: **Cases**, then a separated Admin group **Users · Audit log · Control Center ↗** (Control Center link only when opened on the host computer). Top bar: case switcher, user menu (Change password, Sign out). PST library, Jobs, Search, Ask and Settings appear in later phases; they aren't shown as dead links now.

**3. Case list.** Table: Name, Reference, Client, Files, People, Created, Status. Search box filters by name, reference or client; sortable columns; "Show archived" switch. Admin gets **New case** (dialog: name required; reference, client, description optional). States: empty for Admin ("No cases yet. Create your first case." with the button), empty for others ("No cases are assigned to you yet. Ask the Admin to add you to a case."), loading skeleton at final size.

**4. Case page.** Header: case name (22px), reference and client in muted text, Archived badge when archived, case menu (Rename, Edit details, Archive, Delete; Admin only). Tabs: **Files · Details · People · Activity** (Search, Ask, Review join in later phases).
- **Files:** folder tree on the left (from the uploaded folder structure, with counts), file table on the right: Name, Folder, Type, Size, Added, Added by, Status, Tags. Search by name, filter by type and status, sort any column. Row opens a side drawer with details: SHA-256 (mono, copy button), original path, size, added by and when, tags (editable), Download original. Status is **Stored** in this phase ("Converted" arrives with phase 3), plus **Duplicate** (violet, "Duplicate of <file>") and **Unsupported**.
- **What to do next** panel on the right of Files: "Upload files or a folder" until there are files; then "Conversion to Markdown arrives in the next version."
- **Details:** name, reference, client, description, notes, created date and by. Editable by Admin and assigned Editors.
- **People:** who is assigned and their role. Admin adds or removes people here.
- **Activity:** this case's audit trail (who viewed, uploaded, downloaded, changed what, when).

**5. Upload.** On the Files tab: a drop zone (empty state: "No files yet. Drag files or a folder here, or choose Upload files / Upload folder") plus the two buttons. Folders keep their subfolder structure. During upload: a fixed-height panel listing the batch with one overall count ("Uploading 12 of 80 files") and per-file rows. Each file ends as **Stored**, **Duplicate** (already in this case, not imported again, links to the existing file), **Unsupported type** (e.g. ".zip isn't supported; supported: .docx, .pdf, .msg, .eml, .txt and images") or **Failed** with the reason and Retry. A failed file never stops the rest of the batch. Accepted types: .docx, .pdf, .msg, .eml, .txt, .png, .jpg, .jpeg, .tif, .tiff. Size limit per file: 2 GB (PSTs go to the PST library in a later phase).

**6. Users (Admin).** Table: Name, Username, Role, Cases, Last sign-in, Status. **Add person** dialog: name, username, role, cases to assign. It creates a **temporary password** shown once with a copy button, to pass on in person; they choose their own on first sign-in. Row actions: change role, edit case access, reset password (new temporary password), deactivate / reactivate, unlock. The Admin row can't be deactivated or demoted.

**7. Audit log (Admin).** Table: When, Who, Action, Case, Details, filterable by person, action, case and date. Entries are hash-chained like the launcher's audit file, and the page says so ("Tamper check: all 1,204 entries verified").

## States every screen covers

Empty with the next step, loading at final size, error (what happened, why, what to do), permission denied ("You don't have access to this case"), and both themes. Screens are checked at 1440px and 1024px before the phase is called done, as in phase 1.

## Not in this phase

Conversion to Markdown, OCR, chunking and search (phase 3), PST library, AI features and provider settings. Wizard steps 6 and 7 (first case, invite users) will link to the main app's New case and Add person screens.

## Decisions for you

1. Approve the plan, or tell me what to change.
2. Only the Admin creates cases (my default), or Editors too?
