# Installing Case File Manager on Windows

You do this once. After it, everything happens in your browser. No Docker is needed.

## Before you start

- Windows 10 or 11, 64-bit, signed in with an administrator account.
- At least 20 GB free on the drive that will hold case files.
- An internet connection for the install. It downloads Python, Tesseract OCR and Ollama (about 2 GB in total) from their official release pages. Every download is checked against a fixed checksum before it is used.
- **Recommended:** BitLocker turned on for that drive. The app stores originals, Markdown and search indexes as ordinary files.

## Install

1. Download the install zip and extract it anywhere, for example your Downloads folder. You get a folder containing `install.ps1` and `launcher.exe`.
2. Open **PowerShell as administrator** (right-click it, then *Run as administrator*).
3. Run these commands in that same window:

   ```powershell
   cd $HOME\Downloads\windows
   Set-ExecutionPolicy -Scope Process Bypass -Force
   Get-ChildItem -Recurse | Unblock-File
   .\install.ps1
   ```

   The execution policy line only applies to this PowerShell window and changes nothing permanently.

   Options: `-InstallDir D:\CaseFiles`, `-AppPort 443`, `-ControlCenterPort 8443`.

4. The first install downloads about 2 GB. When it finishes, the script shows a **setup code** and opens the Control Center at `https://localhost:8443`.
5. Your browser warns about the certificate. See [trust-certificate.md](trust-certificate.md) to trust it once, or continue past the warning on this computer for now.
6. Enter the setup code and follow the setup wizard.

Run `install.ps1` again at any time to repair or upgrade. It keeps your cases, Admin account and audit log.

## What gets installed

| Path | Contents | Who can read it |
|---|---|---|
| `C:\CaseFiles\launcher.exe` | The launcher and the service host | Administrators, SYSTEM and the three app services |
| `C:\CaseFiles\app\`, `runtime\` | The web app, Python and Ollama | Administrators, SYSTEM and the three app services |
| `C:\Program Files\Tesseract-OCR\` | Tesseract OCR (its installer always uses this folder) | Everyone can read; only administrators can change it |
| `C:\CaseFiles\launcher\` | Admin password hash, audit log, launcher settings and log | Administrators and SYSTEM only |
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

Run `C:\CaseFiles\uninstall.ps1` as administrator. It removes the services and firewall rules and leaves your case data, audit log and certificates in place. Tesseract OCR stays installed; remove it from *Settings › Apps* if nothing else uses it.

## Backup

Back up the whole `C:\CaseFiles` folder while the services are stopped (Control Center › Stop all). The `data` folder holds the cases; the `launcher` and `certs` folders hold the audit log and certificates.

## Known limits in this version

- Tested automatically on a fresh Windows Server machine in CI. Not yet tested on Windows 10 or 11 desktops with antivirus software other than Microsoft Defender.
- A Windows virtual account can't be used for internet proxies that need a signed-in user. If your office uses such a proxy, downloading AI models may fail; tell Claude.
