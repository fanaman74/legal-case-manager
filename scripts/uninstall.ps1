<#
.SYNOPSIS
  Removes the Case File Manager launcher service, containers and firewall rule.
  Case data in the data folder is NOT deleted.
#>
[CmdletBinding()]
param([string]$InstallDir = "C:\CaseFiles")
$ErrorActionPreference = "Continue"
Stop-Service -Name "CaseFileManagerLauncher" -ErrorAction SilentlyContinue
& (Join-Path $InstallDir "launcher.exe") service uninstall
& docker compose --project-name casefiles --file "$InstallDir\deploy\compose.yaml" down
Get-NetFirewallRule -DisplayName "Case File Manager web app" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "Removed the service, containers and firewall rule. Your case data is still in $InstallDir\data."
