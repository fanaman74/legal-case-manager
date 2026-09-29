<#
.SYNOPSIS
  One-time install of Case File Manager on Windows.

.DESCRIPTION
  Run from the folder produced by `scripts/build.sh windows`, in PowerShell
  opened with "Run as administrator". After this, everything is done in the
  browser at https://localhost:8443.

  What it does:
    1. Checks Docker Desktop is installed and running, and there is disk space.
    2. Creates C:\CaseFiles (or -InstallDir) and locks down the launcher and
       certificate folders to Administrators and SYSTEM.
    3. Copies the launcher and service definitions, writes launcher.json and
       deploy\.env.
    4. Builds and downloads the service images (this takes a while once).
    5. Opens the web app port on Private networks only in Windows Firewall.
    6. Registers the launcher as a Windows service that starts at boot.
    7. Shows the one-time setup code and opens the Control Center.
#>
[CmdletBinding()]
param(
  [string]$InstallDir = "C:\CaseFiles",
  [int]$AppPort = 443,
  [int]$ControlCenterPort = 8443
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step($n, $text) { Write-Host ""; Write-Host "[$n/7] $text" -ForegroundColor Cyan }
function Fail($what, $next) {
  Write-Host ""
  Write-Host "Install stopped: $what" -ForegroundColor Red
  Write-Host "Next step: $next" -ForegroundColor Yellow
  exit 1
}

# --- Administrator check ----------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Fail "this script needs administrator rights." "Right-click PowerShell, choose 'Run as administrator', and run install.ps1 again."
}
if (-not (Test-Path (Join-Path $here "launcher.exe"))) {
  Fail "launcher.exe isn't next to this script." "Run install.ps1 from the folder produced by 'scripts/build.sh windows'."
}

# --- 1. Prerequisites -------------------------------------------------------
Step 1 "Checking prerequisites"
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
  Fail "Docker Desktop isn't installed." "Install Docker Desktop from https://www.docker.com/products/docker-desktop/, start it, then run this script again."
}
& docker version --format "{{.Server.Version}}" *> $null
if ($LASTEXITCODE -ne 0) {
  Fail "Docker Desktop isn't running." "Start Docker Desktop, wait until it shows 'Engine running', then run this script again."
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
Write-Host "Docker is running and $freeGB GB is free."

# --- 2. Folders and permissions ---------------------------------------------
Step 2 "Creating $InstallDir"
foreach ($d in @("", "data", "launcher", "certs")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $d) | Out-Null
}
# The launcher's credentials and audit log: Administrators and SYSTEM only.
# The certs folder also needs read access for the signed-in user, because
# Docker Desktop (which runs as that user) mounts server.crt and server.key.
# The data folder keeps inherited permissions so Docker can mount it.
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls (Join-Path $InstallDir "launcher") /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null
& icacls (Join-Path $InstallDir "certs") /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "${me}:(OI)(CI)R" | Out-Null

# --- 3. Files ---------------------------------------------------------------
Step 3 "Copying the launcher and service definitions"
Copy-Item (Join-Path $here "launcher.exe") $InstallDir -Force
foreach ($d in @("deploy", "services")) {
  Copy-Item (Join-Path $here $d) $InstallDir -Recurse -Force
}
$fwd = $InstallDir -replace "\\", "/"
$config = [ordered]@{
  data_dir     = "$InstallDir\data"
  state_dir    = "$InstallDir\launcher"
  certs_dir    = "$InstallDir\certs"
  compose_file = "$InstallDir\deploy\compose.yaml"
  port         = $ControlCenterPort
  app_port     = $AppPort
  docker       = $docker.Source
}
$config | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $InstallDir "launcher.json")
@(
  "CFM_DATA_DIR=$fwd/data",
  "CFM_CERTS_DIR=$fwd/certs",
  "CFM_APP_PORT=$AppPort",
  "CFM_VERSION=0.1.0-dev"
) | Set-Content -Encoding ASCII (Join-Path $InstallDir "deploy\.env")

# --- 4. Images --------------------------------------------------------------
Step 4 "Building and downloading the service images (this takes several minutes the first time)"
$compose = @("compose", "--project-name", "casefiles", "--file", "$InstallDir\deploy\compose.yaml")
& docker @compose pull queue models
if ($LASTEXITCODE -ne 0) { Fail "the database and model images couldn't be downloaded." "Check the internet connection, then run this script again." }
& docker @compose build
if ($LASTEXITCODE -ne 0) { Fail "the service images couldn't be built." "Check the internet connection and that Docker Desktop is running, then run this script again." }

# --- 5. Firewall ------------------------------------------------------------
Step 5 "Allowing the web app on private networks only"
$ruleName = "Case File Manager web app"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $AppPort -Action Allow -Profile Private | Out-Null
Write-Host "Port $AppPort is open on Private networks. Public and Domain networks stay closed."
Write-Host "The Control Center (port $ControlCenterPort) only listens on this computer and needs no rule."

# --- 6. Service -------------------------------------------------------------
Step 6 "Registering the launcher to start with Windows"
$exe = Join-Path $InstallDir "launcher.exe"
$existing = Get-Service -Name "CaseFileManagerLauncher" -ErrorAction SilentlyContinue
if ($existing) {
  Stop-Service -Name "CaseFileManagerLauncher" -ErrorAction SilentlyContinue
  & $exe service uninstall | Out-Null
  Start-Sleep -Seconds 2
}
& $exe --config (Join-Path $InstallDir "launcher.json") service install
if ($LASTEXITCODE -ne 0) { Fail "the Windows service couldn't be registered." "Run this script again as administrator." }
Start-Service -Name "CaseFileManagerLauncher"

# --- 7. Setup code ----------------------------------------------------------
Step 7 "Finishing"
$codeFile = Join-Path $InstallDir "launcher\setup-code.txt"
for ($i = 0; $i -lt 30 -and -not (Test-Path $codeFile); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $codeFile) {
  $code = (Get-Content $codeFile | Select-String -Pattern "^[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$").Line
  Write-Host ""
  Write-Host "Your one-time setup code:  $code" -ForegroundColor Green
  Write-Host "(also saved in $codeFile)"
} else {
  Write-Host "The setup code isn't ready yet. Run '$exe --config $InstallDir\launcher.json setup-code' in a minute to see it." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "In Docker Desktop, open Settings > General and turn on 'Start Docker Desktop when you sign in',"
Write-Host "so the services come back after a restart."
Write-Host ""
Write-Host "Opening the Control Center: https://localhost:$ControlCenterPort"
Write-Host "Your browser will warn about the certificate the first time. See docs\trust-certificate.md to trust it."
Start-Process "https://localhost:$ControlCenterPort"
