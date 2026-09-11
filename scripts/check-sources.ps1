[CmdletBinding()]param()
$ErrorActionPreference='Continue';$Root=Split-Path -Parent $PSScriptRoot;$Failed=@()
Get-ChildItem (Join-Path $Root 'modules') -Directory|Sort-Object Name|ForEach-Object{
 Write-Host "`n=== $($_.Name) ===";& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $_.FullName 'src\prepare.ps1') -CheckOnly -NonInteractive
 if($LASTEXITCODE -ne 0){$Failed+=$_.Name}
}
if($Failed){Write-Host "Missing inputs: $($Failed -join ', ')";exit 1};Write-Host '[OK] every module source set is complete'
