# Installing Case File Manager on Windows

You do this once. After it, everything happens in your browser.

## Before you start

- Windows 10 or 11 (64-bit), signed in with an administrator account.
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running ("Engine running" in its window).
  Docker Desktop is free for personal use and for businesses under 250 staff and under $10M revenue.
- At least 20 GB free on the drive that will hold case files.
- **Recommended:** BitLocker turned on for that drive. The app stores originals, Markdown and search indexes as ordinary files.

## Install

1. Get the install folder. On a computer with Go and Node.js, run `scripts/build.sh windows` from the repository; it creates `dist/windows`. Copy that folder to the Windows computer.
2. Open **PowerShell as administrator** (right-click, *Run as administrator*).
3. Run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   cd path\to\windows
   .\install.ps1
   ```

   Options: `-InstallDir D:\CaseFiles`, `-AppPort 443`, `-ControlCenterPort 8443`.

4. Building the images takes several minutes the first time. When it finishes, the script shows a **setup code** and opens the Control Center at `https://localhost:8443`.
5. Your browser warns about the certificate. See [trust-certificate.md](trust-certificate.md) to trust it once, or continue past the warning on this computer for now.
6. Enter the setup code and follow the setup wizard.

In Docker Desktop, turn on **Settings › General › Start Docker Desktop when you sign in**, so the services come back after a restart.

## What gets installed

| Path | Contents | Who can read it |
|---|---|---|
| `C:\CaseFiles\launcher.exe` | The launcher (a Windows service named *Case File Manager launcher*) | Everyone |
| `C:\CaseFiles\launcher\` | Admin password hash, audit log, launcher settings and log | Administrators and SYSTEM only |
| `C:\CaseFiles\certs\` | Local certificate authority and the HTTPS certificate | Administrators, SYSTEM, and the installing user (read) |
| `C:\CaseFiles\data\` | Case files, database, search index, job queue, AI models | Mounted into the app containers |
| `C:\CaseFiles\deploy\`, `services\` | Service definitions | Everyone |

A Windows Firewall rule opens the web app port on **Private** networks only. The Control Center listens on this computer only unless you turn on network access in it.

## Restarts

The launcher starts with Windows. Docker Desktop starts when you sign in, and the services start with it. If the computer restarts and nobody signs in, the Control Center shows *Docker isn't running* until someone does.

## Uninstall

Run `C:\CaseFiles\...\uninstall.ps1` as administrator. It removes the service, containers and firewall rule and leaves your case data in place.

## Backup

Back up the whole `C:\CaseFiles` folder while the services are stopped (Control Center › Stop all). The `data` folder holds the cases; the `launcher` and `certs` folders hold the audit log and certificates.

## Known limits in this version

- The launcher runs as the Windows SYSTEM account and talks to Docker Desktop through its named pipe. This has not been tested on a real Windows machine yet. If the Control Center shows *Docker isn't running* while Docker Desktop is clearly running, tell Claude; the fix is to run the launcher as your user through Task Scheduler.
