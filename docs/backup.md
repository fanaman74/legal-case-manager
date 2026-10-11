# Backing up and restoring Case File Manager

Everything the app knows lives in three folders under `C:\CaseFiles`. Back them up regularly, keep several copies, and try a restore now and then. Nothing here needs PowerShell or a command line: you use the Control Center and File Explorer, or your firm's backup software.

## What to back up

| Folder | What's in it | Back up? |
|---|---|---|
| `C:\CaseFiles\data` | Every case: originals, Markdown, page images, search indexes, mailboxes. Also `app.db` (people, cases, files, tags, AI settings and reviews, and the audit log). | **Yes** |
| `C:\CaseFiles\launcher` | The Admin password, the launcher's own audit log and settings. | **Yes** |
| `C:\CaseFiles\certs` | The local certificate authority and HTTPS certificate. Without it, the app and every browser have to trust a new certificate after a restore. | **Yes** |
| `C:\CaseFiles\data\models` | Local AI models. Large, and downloaded again from the Control Center if missing. | Optional |
| `C:\CaseFiles\data\exports` | Case exports waiting to be downloaded. Deleted after 7 days anyway. | No |
| `C:\CaseFiles\app`, `runtime`, `logs` | The programs and their logs. The installer puts the programs back. | No |

The folders hold confidential client material. Back them up only to places at least as secure as this computer: an encrypted external drive (BitLocker To Go), or your firm's backup server. Don't point a sync service such as OneDrive or Dropbox at the live `data` folder: it copies files while they're being written and sends case files to the cloud.

## Making a backup

**The simple way (an external drive):**

1. Open the Control Center from the desktop shortcut and choose **Stop all**. People can't use the app until you start it again. Stopping it means the database isn't copied halfway through a change.
2. In File Explorer, copy `C:\CaseFiles\data`, `C:\CaseFiles\launcher` and `C:\CaseFiles\certs` to the backup drive, into a folder named with today's date. Windows asks for administrator approval for `launcher` and `certs`; choose **Continue**.
3. Back in the Control Center, choose **Start all**.

**With backup software:** software that uses Windows snapshots (Volume Shadow Copy), such as Windows Server Backup, Veeam or your IT provider's agent, can back up the three folders while the app runs. The database is designed to recover from such a snapshot. Stopping the app first is still the safest, so schedule the backup for the night if you can, and stop and start the app from the Control Center around it.

**How often and how many:** at least every working day. Keep several days' copies, not just the latest, so a copy made after something went wrong (a deleted case, ransomware) doesn't overwrite the good ones. Keep one recent copy disconnected from the computer.

## Restoring

On the same computer, or a new one:

1. If the app isn't installed there yet, install it as in [install-windows.md](install-windows.md). Whatever Admin account you create in the setup wizard is replaced by your old one when you restore.
2. In the Control Center, choose **Stop all**.
3. In File Explorer, replace `C:\CaseFiles\data`, `C:\CaseFiles\launcher` and `C:\CaseFiles\certs` with the copies from the backup.
4. Run the Case File Manager installer and choose **Yes** (or double-click `C:\CaseFiles\launcher.exe`). It repairs the folder permissions for the restored files and starts everything.
5. Sign in as the Admin and check:
   - The **Audit log** page shows "Tamper check passed".
   - A case opens and a document shows its Markdown.
   - **Settings**: on a different computer, **API keys for AI services must be entered again** (see below). The rest of the AI settings are kept.
   - If search finds nothing in a case, use **Settings › Search model › Re-index all cases**. Search rebuilds from the stored Markdown; the originals aren't touched.

If the local AI models weren't backed up, the Control Center's **Components** section downloads them again.

## Why API keys need entering again on a new computer

Keys for Claude, OpenAI and the other AI services are sealed with Windows Data Protection for the web app's account on this computer. A copy of the database is no use to anyone who takes it elsewhere, and that includes you on a new computer. Restoring onto the same computer keeps them working.

## Case exports are not backups

**Export case as ZIP** (Case actions, Admin only) makes one case's files, Markdown, details and audit trail into a single ZIP for handing over or archiving. It doesn't include people, passwords or settings, and it can't be imported back into the app. Use it alongside backups, not instead of them.
