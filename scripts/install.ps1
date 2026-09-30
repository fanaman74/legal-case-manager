<#
.SYNOPSIS
  One-time install of Case File Manager on Windows. No Docker needed.

.DESCRIPTION
  Run from the folder produced by `scripts/build.sh windows` (or the install
  zip), in PowerShell opened with "Run as administrator". This is only needed
  once: Windows requires an administrator to register the launcher as a
  service. Everything else happens in the Control Center at
  https://localhost:8443, which checks and installs Python, Tesseract OCR,
  Ollama and the embedding model by itself, then starts every service.

  Running it again repairs or upgrades the launcher and keeps your cases.

  What it does:
    1. Checks this is a 64-bit Windows with enough disk space.
    2. Stops the services if they are already installed.
    3. Copies the launcher and the app into C:\CaseFiles (or -InstallDir).
    4. Registers the Windows services. The launcher runs as SYSTEM; each app
       service runs under its own restricted account (NT SERVICE\CaseFiles-api,
       -worker and -models) with no password and no admin rights.
    5. Creates the certificates and the one-time setup code.
    6. Sets folder permissions so each service can only reach what it needs.
    7. Opens the web app port on Private networks only, and blocks the
       background worker from the internet.
    8. Starts the launcher and shows the setup code.
#>
[CmdletBinding()]
param(
  [string]$InstallDir = "C:\CaseFiles",
  [int]$AppPort = 443,
  [int]$ControlCenterPort = 8443,
  # Don't open the browser at the end (used by automated tests).
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$steps = 8

function Step($n, $text) { Write-Host ""; Write-Host "[$n/$steps] $text" -ForegroundColor Cyan }
function Fail($what, $next) {
  Write-Host ""
  Write-Host "Install stopped: $what" -ForegroundColor Red
  Write-Host "Next step: $next" -ForegroundColor Yellow
  exit 1
}
function Invoke-Checked($what, $next, [scriptblock]$block) {
  & $block
  if ($LASTEXITCODE -ne 0) { Fail $what $next }
}

$services = @("api", "worker", "models")
$launcherService = "CaseFileManagerLauncher"
$admins = "*S-1-5-32-544"
$system = "*S-1-5-18"
function Account($id) { "NT SERVICE\CaseFiles-$id" }

# --- Administrator check ----------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Fail "this script needs administrator rights." "Right-click PowerShell, choose 'Run as administrator', and run install.ps1 again."
}
foreach ($f in @("launcher.exe", "app\requirements-windows.txt")) {
  if (-not (Test-Path (Join-Path $here $f))) {
    Fail "$f isn't next to this script." "Extract the whole install zip, then run install.ps1 from the extracted folder."
  }
}

# --- 1. Prerequisites -------------------------------------------------------
Step 1 "Checking this computer"
if (-not [Environment]::Is64BitOperatingSystem) {
  Fail "this is a 32-bit version of Windows." "Case File Manager needs 64-bit Windows 10 or 11."
}
$drive = (Split-Path -Qualifier $InstallDir).TrimEnd(":")
$freeGB = [math]::Round((Get-PSDrive $drive).Free / 1GB, 1)
if ($freeGB -lt 20) {
  Fail "only $freeGB GB is free on drive ${drive}:." "Free up space so at least 20 GB is available, or pass -InstallDir on a bigger drive."
}
$bitlocker = Get-BitLockerVolume -MountPoint "${drive}:" -ErrorAction SilentlyContinue
if ($bitlocker -and $bitlocker.ProtectionStatus -ne "On") {
  Write-Host "Warning: BitLocker is off for drive ${drive}:. Case files are stored unencrypted on this disk." -ForegroundColor Yellow
  Write-Host "         Turning on BitLocker is strongly recommended for confidential material." -ForegroundColor Yellow
}
Write-Host "64-bit Windows, $freeGB GB free on drive ${drive}:."

# --- 2. Stop running services (repair or upgrade) ---------------------------
Step 2 "Stopping Case File Manager if it's running"
$names = @($launcherService) + ($services | ForEach-Object { "CaseFiles-$_" })
$found = $false
foreach ($n in $names) {
  $s = Get-Service -Name $n -ErrorAction SilentlyContinue
  if ($s) {
    $found = $true
    if ($s.Status -ne "Stopped") { Stop-Service -Name $n -Force -ErrorAction SilentlyContinue }
  }
}
if ($found) { Start-Sleep -Seconds 2; Write-Host "Stopped the existing services. Your cases are kept." } else { Write-Host "Nothing installed yet." }

# --- 3. Files ---------------------------------------------------------------
Step 3 "Copying the launcher and the app to $InstallDir"
$runtime = Join-Path $InstallDir "runtime"
$appDir = Join-Path $InstallDir "app"
$dataDir = Join-Path $InstallDir "data"
$logDir = Join-Path $InstallDir "logs"
$certsDir = Join-Path $InstallDir "certs"
$stateDir = Join-Path $InstallDir "launcher"
foreach ($d in @($InstallDir, $runtime, $dataDir, (Join-Path $dataDir "models"), (Join-Path $dataDir ".run"), $logDir, $certsDir, $stateDir)) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Copy-Item (Join-Path $here "launcher.exe") $InstallDir -Force
if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
Copy-Item (Join-Path $here "app") $appDir -Recurse -Force
Copy-Item (Join-Path $here "uninstall.ps1") $InstallDir -Force

# Python lives here once the launcher has installed it; the firewall rule
# below names it.
$python = Join-Path $runtime "python\python.exe"

# --- 4. Configuration and services ------------------------------------------
Step 4 "Registering the Windows services"
$configPath = Join-Path $InstallDir "launcher.json"
$config = [ordered]@{
  data_dir    = $dataDir
  state_dir   = $stateDir
  certs_dir   = $certsDir
  app_dir     = $appDir
  runtime_dir = $runtime
  log_dir     = $logDir
  supervisor  = "windows"
  port        = $ControlCenterPort
  app_port    = $AppPort
}
$config | ConvertTo-Json | Set-Content -Encoding UTF8 $configPath
$exe = Join-Path $InstallDir "launcher.exe"
Invoke-Checked "the Windows services couldn't be registered." "Run install.ps1 again as administrator." {
  & $exe --config $configPath service install
}
Write-Host "Registered the launcher and the Web app, Background worker and Local AI models services."

# --- 5. Certificates and setup code -----------------------------------------
Step 5 "Creating the HTTPS certificates and the setup code"
Invoke-Checked "the certificates couldn't be created." "Check free disk space, then run install.ps1 again." {
  & $exe --config $configPath prepare
}

# --- 6. Permissions ---------------------------------------------------------
Step 6 "Setting folder permissions"
function Set-Acl-Exact($path, [string[]]$grants) {
  # Reset, then keep only the listed permissions (no inherited ones).
  & icacls $path /reset /T /C /Q | Out-Null
  $icaclsArgs = @($path, "/inheritance:r", "/grant:r") + $grants + @("/C", "/Q")
  & icacls @icaclsArgs | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "permissions on $path couldn't be set." "Run install.ps1 again as administrator." }
}
$full = @("${admins}:(OI)(CI)F", "${system}:(OI)(CI)F")
$readApp = $services | ForEach-Object { "$(Account $_):(OI)(CI)RX" }
# The install folder: the services can read and run the app and runtimes.
Set-Acl-Exact $InstallDir ($full + $readApp)
# Launcher credentials, audit log and the certificate authority: nobody else.
Set-Acl-Exact $stateDir $full
Set-Acl-Exact $certsDir $full
# The web app may read its own certificate and key, never the CA key.
foreach ($f in @("server.crt", "server.key")) {
  & icacls (Join-Path $certsDir $f) /grant "$(Account 'api'):R" /C /Q | Out-Null
}
# Case data: the web app and worker. The models service only gets its folder.
Set-Acl-Exact $dataDir ($full + @("$(Account 'api'):(OI)(CI)M", "$(Account 'worker'):(OI)(CI)M"))
& icacls (Join-Path $dataDir "models") /grant "$(Account 'models'):(OI)(CI)M" /C /Q | Out-Null
# Logs: each service writes only its own folder.
Set-Acl-Exact $logDir $full
foreach ($id in $services) {
  $d = Join-Path $logDir $id
  New-Item -ItemType Directory -Force -Path $d | Out-Null
  & icacls $d /grant "$(Account $id):(OI)(CI)M" /C /Q | Out-Null
}
Write-Host "Each service can reach only its own files. The launcher's credentials and the certificate authority are Administrators-only."

# --- 7. Firewall ------------------------------------------------------------
Step 7 "Setting firewall rules"
$group = "Case File Manager"
Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName "Case File Manager web app" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Group $group -DisplayName "Case File Manager web app" -Direction Inbound -Protocol TCP `
  -LocalPort $AppPort -Program $python -Action Allow -Profile Private | Out-Null
# The worker reads every case file, so it never gets to the internet. It can
# still reach the local AI models on this computer (loopback isn't blocked).
New-NetFirewallRule -Group $group -DisplayName "Case File Manager worker: no internet" -Direction Outbound `
  -Service "CaseFiles-worker" -Action Block `
  -RemoteAddress @("0.0.0.0-126.255.255.255", "128.0.0.0-255.255.255.255", "::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff") | Out-Null
Write-Host "Port $AppPort is open on Private networks only. Public and Domain networks stay closed."
Write-Host "The background worker can't reach the internet. The Control Center (port $ControlCenterPort) only listens on this computer."

# --- 8. Start ---------------------------------------------------------------
Step 8 "Starting the launcher"
Start-Service -Name $launcherService
$up = $false
for ($i = 0; $i -lt 60 -and -not $up; $i++) {
  try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", $ControlCenterPort)
    $c.Close()
    $up = $true
  } catch { Start-Sleep -Seconds 1 }
}
if (-not $up) {
  Fail "the launcher didn't start." "Open $stateDir\launcher.log for the reason, then run install.ps1 again."
}

$codeFile = Join-Path $stateDir "setup-code.txt"
Write-Host ""
if (Test-Path $codeFile) {
  $code = (Get-Content $codeFile | Select-String -Pattern "^[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$").Line
  Write-Host "Your one-time setup code:  $code" -ForegroundColor Green
  Write-Host "(also saved in $codeFile, which only Administrators can open)"
} else {
  Write-Host "Setup is already complete. Sign in with your Admin account." -ForegroundColor Green
}
Write-Host ""
Write-Host "The launcher is now downloading and installing Python, Tesseract OCR, Ollama and the"
Write-Host "embedding model (about 3 GB), then it starts every service. Watch it in the Control Center."
Write-Host "The services start with Windows from now on, even when nobody is signed in."
Write-Host "Open the Control Center at https://localhost:$ControlCenterPort"
Write-Host "Your browser will warn about the certificate the first time. README-install.md explains how to trust it."
if (-not $NoBrowser) { Start-Process "https://localhost:$ControlCenterPort" }
