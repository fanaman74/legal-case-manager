# Installing Case File Manager on Windows

You double-click one file. After that, everything happens in the Case File Manager app, including installing Python, OCR and the local AI. There is nothing to type and no PowerShell. No Docker is needed. Everything runs on this computer.

## Before you start

- Windows 10 or 11, 64-bit, signed in with an administrator account (or someone who can approve the Windows permission prompt).
- At least 20 GB free on the drive that will hold case files.
- An internet connection for the first start. The launcher downloads Python, Tesseract OCR, Ollama and the embedding model (about 3 GB in total) from their official sources. Every program download is checked against a fixed checksum before it is used.
- **Recommended:** BitLocker turned on for that drive. The app stores originals, Markdown and search indexes as ordinary files. Setup warns you if it's off.

## Install

1. Double-click **Case File Manager Setup.exe**. If Windows shows "Windows protected your PC", choose **More info**, then **Run anyway** (the file isn't code-signed yet).
2. Windows asks whether to allow changes. Choose **Yes**.
3. A progress bar runs for about a minute while the services are set up. Then the app opens, with the Control Center already past the setup code.
4. The Control Center's **Components** section shows the launcher downloading and installing Python, Tesseract OCR, Ollama and the embedding model. When they're done, it starts every service by itself. Then create your Admin account in the setup wizard.
5. The case manager window switches from "isn't answering yet" to the sign-in page by itself once the web app is running.

There's no certificate warning in the app: it trusts only the certificate authority Case File Manager created on this computer. Colleagues who open the case manager in a browser on another computer still see the warning until they trust the CA once ([trust-certificate.md](trust-certificate.md)).

The installer puts **Case File Manager** on the desktop and in the Start menu. Open the Control Center from the app's **File** menu (Ctrl+2) or from the tray icon by the clock. The tray icon is green when the case manager is answering, amber when only the launcher is (services starting or stopped), and red when neither is.

If the Control Center asks for the setup code (for example if the app was closed before you finished), the code is in `C:\CaseFiles\launcher\setup-code.txt`, which only administrators can open.

### Using the app

- Closing the app stops nothing. The services keep running in the background, so conversions and indexing finish and colleagues on other computers can keep working.
- When background work you started runs out while the app isn't in front, Windows shows a notification. After a download (such as a case export) is saved, a notification offers to show it in its folder.
- The app only opens this computer's case manager and Control Center. Any other link opens in your normal browser, and the app window never loads anything from the internet.

## Checking and repairing components

The Control Center's **Components** section lists each program the app needs, its version and whether it's installed. Use **Install**, **Update** or **Reinstall** on a row, or **Install what's missing** for all of them. The launcher stops the services that use a program while it's replaced and starts them again afterwards. It also installs anything missing each time it starts. Every install is recorded in the audit log.

To upgrade, double-click the newer **Case File Manager Setup.exe**. It keeps your cases, accounts and audit log. The app doesn't update itself, because that would mean checking a server on the internet. If the new version pins newer programs, the launcher updates them when it starts.

## What gets installed

| Path | Contents | Who can read it |
|---|---|---|
| `C:\Program Files\Case File Manager\` | The desktop app and a copy of the local CA certificate (public, not secret) | Everyone can read; only administrators can change it |
| `C:\CaseFiles\launcher.exe` | The launcher and the service host (double-click it to repair) | Administrators, SYSTEM and the three app services |
| `C:\CaseFiles\app\`, `runtime\` | The web app, Python and Ollama | Administrators, SYSTEM and the three app services |
| `C:\Program Files\Tesseract-OCR\` | Tesseract OCR (its installer always uses this folder) | Everyone can read; only administrators can change it |
| `C:\CaseFiles\launcher\` | Admin password hash, audit log, launcher settings and log, verified downloads | Administrators and SYSTEM only |
| `C:\CaseFiles\certs\` | Local certificate authority and the HTTPS certificate | Administrators and SYSTEM. The web app can read only its own certificate and key. |
| `C:\CaseFiles\data\` | Case files, database, search index, AI models | Administrators, SYSTEM, the web app and the worker. The models service can only reach `data\models`. |
| `C:\CaseFiles\logs\` | One folder per service | Administrators and SYSTEM. Each service can write only its own folder. |

## Windows services

| Service | Runs as | Starts |
|---|---|---|
| Case File Manager launcher | SYSTEM | At boot |
| Case File Manager Web app | `NT SERVICE\CaseFiles-api` | Started by the launcher |
| Case File Manager Background worker | `NT SERVICE\CaseFiles-worker` | Started by the launcher |
| Case File Manager Local AI models | `NT SERVICE\CaseFiles-models` | Started by the launcher |

The three app services use Windows *virtual accounts*: they have no password, can't be used to sign in, and have no administrator rights.

## Firewall

- The web app port (443) is open on **Private** networks only. Public and Domain networks stay closed. The web app also refuses any visitor whose address isn't on the local network.
- The background worker, which reads every case file, is blocked from the internet. It can still reach the local AI models on the same computer.
- The Control Center listens on this computer only, unless you turn on network access in it.

## Restarts

The launcher starts with Windows, and it starts the services that were running before the restart. Nobody needs to sign in. If a service crashes, it is restarted automatically and the Control Center says so. If it crashes five times in five minutes, it's left stopped, and the Control Center shows what to do.

## Uninstall

Open **Settings › Apps**, find **Case File Manager** and choose **Uninstall**. It removes the app, the services, firewall rules and shortcuts and leaves your case data, audit log and certificates in `C:\CaseFiles`. Tesseract OCR stays installed; remove it from *Settings › Apps* if nothing else uses it.

## Backup

Back up `C:\CaseFiles\data`, `launcher` and `certs` every working day, with the app stopped from the Control Center. [backup.md](backup.md) says how, how to restore, and what to check afterwards.

## Known limits in this version

- Tested automatically on a fresh Windows Server machine in CI. Not yet tested on Windows 10 or 11 desktops with antivirus software other than Microsoft Defender.
- A Windows virtual account can't be used for internet proxies that need a signed-in user. If your office uses such a proxy, downloading AI models may fail; tell Claude.
