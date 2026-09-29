<#
.SYNOPSIS
  One-time install of Case File Manager on Windows. No Docker needed.

.DESCRIPTION
  Run from the folder produced by `scripts/build.sh windows` (or the install
  zip), in PowerShell opened with "Run as administrator". After this,
  everything is done in the browser at https://localhost:8443.

  Running it again repairs or upgrades an installation and keeps your cases.

  What it does:
    1. Checks this is a 64-bit Windows with enough disk space.
    2. Stops the services if they are already installed.
    3. Copies the launcher and the app into C:\CaseFiles (or -InstallDir).
    4. Downloads Python, Tesseract OCR and Ollama from their official
       release pages, checks each file's SHA-256 against runtimes.json, and
       installs the app's Python packages (each one hash-checked too).
    5. Registers the Windows services. The launcher runs as SYSTEM; each app
       service runs under its own restricted account (NT SERVICE\CaseFiles-api,
       -worker and -models) with no password and no admin rights.
    6. Creates the certificates and the one-time setup code.
    7. Sets folder permissions so each service can only reach what it needs.
    8. Opens the web app port on Private networks only, and blocks the
       background worker from the internet.
    9. Starts the launcher and shows the setup code.
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
$ProgressPreference = "SilentlyContinue"   # the progress bar makes downloads very slow in Windows PowerShell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$steps = 9

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
foreach ($f in @("launcher.exe", "runtimes.json", "app\requirements-windows.txt")) {
  if (-not (Test-Path (Join-Path $here $f))) {
    Fail "$f isn't next to this script." "Extract the whole install zip, then run install.ps1 from the extracted folder."
  }
}
$runtimes = Get-Content -Raw (Join-Path $here "runtimes.json") | ConvertFrom-Json

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
$downloads = Join-Path $InstallDir "downloads"
foreach ($d in @($InstallDir, $runtime, $dataDir, (Join-Path $dataDir "models"), (Join-Path $dataDir ".run"), $logDir, $certsDir, $stateDir, $downloads)) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Copy-Item (Join-Path $here "launcher.exe") $InstallDir -Force
if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
Copy-Item (Join-Path $here "app") $appDir -Recurse -Force
Copy-Item (Join-Path $here "uninstall.ps1") $InstallDir -Force

# --- 4. Runtimes ------------------------------------------------------------
Step 4 "Downloading and checking Python, Tesseract OCR and Ollama (large downloads the first time)"

function Get-Verified($name) {
  $r = $runtimes.$name
  if (-not $r.sha256) {
    Fail "runtimes.json has no checksum for $name." "Use an official install zip. This one wasn't finished."
  }
  $dest = Join-Path $downloads $r.file
  if ((Test-Path $dest) -and ((Get-FileHash -Algorithm SHA256 $dest).Hash -eq $r.sha256.ToUpper())) {
    Write-Host "  $name $($r.version): already downloaded"
    return $dest
  }
  Write-Host "  $name $($r.version): downloading from $($r.url)"
  $tmp = "$dest.part"
  if (Test-Path $tmp) { Remove-Item -Force $tmp }
  $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($curl) {
    & $curl.Source -fsSL --retry 3 -o $tmp $r.url
    if ($LASTEXITCODE -ne 0) { Fail "$name couldn't be downloaded." "Check the internet connection, then run install.ps1 again." }
  } else {
    try { Invoke-WebRequest -UseBasicParsing -Uri $r.url -OutFile $tmp } catch { Fail "$name couldn't be downloaded." "Check the internet connection, then run install.ps1 again." }
  }
  $hash = (Get-FileHash -Algorithm SHA256 $tmp).Hash
  if ($hash -ne $r.sha256.ToUpper()) {
    Remove-Item -Force $tmp
    Fail "the $name download doesn't match its checksum, so it wasn't used." "Run install.ps1 again. If this keeps happening, something on the network is changing downloads; tell your IT contact."
  }
  Move-Item -Force $tmp $dest
  return $dest
}

function Expand-To($archive, $target) {
  if (Test-Path $target) { Remove-Item -Recurse -Force $target }
  New-Item -ItemType Directory -Force -Path $target | Out-Null
  $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
  if ($tar) {
    & $tar.Source -xf $archive -C $target
    if ($LASTEXITCODE -ne 0) { Fail "$archive couldn't be unpacked." "Check free disk space, then run install.ps1 again." }
  } else {
    $zip = "$archive.zip"
    Copy-Item -Force $archive $zip
    Expand-Archive -Force -Path $zip -DestinationPath $target
    Remove-Item -Force $zip
  }
}

function Test-Installed($name) {
  $marker = Join-Path $runtime "$name.sha256"
  (Test-Path $marker) -and ((Get-Content -Raw $marker).Trim() -eq $runtimes.$name.sha256)
}
function Set-Installed($name) { Set-Content -Encoding ASCII (Join-Path $runtime "$name.sha256") $runtimes.$name.sha256 }

# Python: the official NuGet package is a plain copy of Python, with no
# installer, registry entries or PATH changes.
$pythonDir = Join-Path $runtime "python"
$python = Join-Path $pythonDir "python.exe"
if (-not (Test-Installed "python") -or -not (Test-Path $python)) {
  $pkg = Get-Verified "python"
  $unpacked = Join-Path $downloads "python-unpacked"
  Expand-To $pkg $unpacked
  if (Test-Path $pythonDir) { Remove-Item -Recurse -Force $pythonDir }
  Move-Item (Join-Path $unpacked "tools") $pythonDir
  Remove-Item -Recurse -Force $unpacked
  Set-Installed "python"
}
Write-Host "  Installing the app's Python packages (each checked against its hash)"
Invoke-Checked "Python's package installer couldn't be set up." "Run install.ps1 again." {
  & $python -m ensurepip --upgrade --default-pip *> $null
}
Invoke-Checked "the app's Python packages couldn't be installed." "Check the internet connection, then run install.ps1 again." {
  & $python -m pip install --disable-pip-version-check --no-input --quiet --no-warn-script-location `
    --require-hashes --only-binary=:all: -r (Join-Path $appDir "requirements-windows.txt")
}

# Tesseract OCR: its official installer, run silently into the app folder.
$tessDir = Join-Path $runtime "tesseract"
if (-not (Test-Installed "tesseract") -or -not (Test-Path (Join-Path $tessDir "tesseract.exe"))) {
  $setup = Get-Verified "tesseract"
  # /D must be last and unquoted (NSIS rule).
  $p = Start-Process -FilePath $setup -ArgumentList "/S", "/D=$tessDir" -Wait -PassThru
  # Some NSIS installers hand the work to a copy of themselves and return
  # early, so give the files a moment to appear.
  $deadline = (Get-Date).AddMinutes(3)
  while (-not (Test-Path (Join-Path $tessDir "tesseract.exe")) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
  if ($p.ExitCode -ne 0 -or -not (Test-Path (Join-Path $tessDir "tesseract.exe"))) {
    $found = @("$env:ProgramFiles\Tesseract-OCR", "${env:ProgramFiles(x86)}\Tesseract-OCR", $tessDir) |
      Where-Object { Test-Path $_ } | ForEach-Object { Get-ChildItem $_ -Filter tesseract.exe -Recurse -ErrorAction SilentlyContinue } |
      Select-Object -First 3 -ExpandProperty FullName
    if ($found) { Write-Host "  Found tesseract.exe at: $($found -join ', ')" }
    Fail "Tesseract OCR didn't install (code $($p.ExitCode))." "Run install.ps1 again. If antivirus software asked about it, allow it."
  }
  Set-Installed "tesseract"
}

# Ollama: the official standalone zip, run by the launcher as a service
# (not the tray app).
$ollamaDir = Join-Path $runtime "ollama"
if (-not (Test-Installed "ollama") -or -not (Test-Path (Join-Path $ollamaDir "ollama.exe"))) {
  $zip = Get-Verified "ollama"
  Expand-To $zip $ollamaDir
  Set-Installed "ollama"
}
Write-Host "Python $($runtimes.python.version), Tesseract $($runtimes.tesseract.version) and Ollama $($runtimes.ollama.version) are installed."

# --- 5. Configuration and services ------------------------------------------
Step 5 "Registering the Windows services"
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

# --- 6. Certificates and setup code -----------------------------------------
Step 6 "Creating the HTTPS certificates and the setup code"
Invoke-Checked "the certificates couldn't be created." "Check free disk space, then run install.ps1 again." {
  & $exe --config $configPath prepare
}

# --- 7. Permissions ---------------------------------------------------------
Step 7 "Setting folder permissions"
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

# --- 8. Firewall ------------------------------------------------------------
Step 8 "Setting firewall rules"
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

# --- 9. Start ---------------------------------------------------------------
Step 9 "Starting the launcher"
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
Write-Host "The services start with Windows from now on, even when nobody is signed in."
Write-Host "Open the Control Center at https://localhost:$ControlCenterPort"
Write-Host "Your browser will warn about the certificate the first time. README-install.md explains how to trust it."
if (-not $NoBrowser) { Start-Process "https://localhost:$ControlCenterPort" }
