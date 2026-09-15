$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $PSScriptRoot '_build\package'
$Out = Join-Path $PSScriptRoot 'prepared\local-llm-stack-2026.09.11.zip'
$ModuleNames = @('mysql','elasticsearch','silo','valkey','llama-cpp','paddleocr','ragflow','web')

$MissingArchives = @()
foreach ($Name in $ModuleNames) {
    $ModuleRoot = Join-Path (Join-Path $Root 'modules') $Name
    $ManifestPath = Join-Path $ModuleRoot 'module.json'
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Module manifest is missing: $ManifestPath"
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $Archive = Join-Path $ModuleRoot ("prepared\local-llm-{0}-{1}.7z" -f $Manifest.name,$Manifest.version)
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf) -or (Get-Item -LiteralPath $Archive).Length -eq 0) {
        Write-Host "[MISSING] $Archive" -ForegroundColor Yellow
        $MissingArchives += $Name
    }
}
if ($MissingArchives.Count -gt 0) {
    throw "Module archives are not prepared: $($MissingArchives -join ', '). CHECK-SOURCES.bat checks downloads only; run modules\<name>\PREPARE-ONLINE.bat for every missing module, then run PREPARE-STACK.bat again."
}

if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage,(Split-Path $Out) -Force | Out-Null
try {
    Copy-Item -LiteralPath (Join-Path $Root 'LOCAL-LLM.bat'),(Join-Path $Root 'README.md'),(Join-Path $Root 'LICENSE') -Destination $Stage
    $StackStage = Join-Path $Stage 'stack'
    New-Item -ItemType Directory -Path $StackStage | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'control.ps1'),(Join-Path $PSScriptRoot 'README.md') -Destination $StackStage
    if (Test-Path -LiteralPath $Out) { Remove-Item -LiteralPath $Out -Force }
    Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath $Out -CompressionLevel Optimal
    Write-Host "[OK] Prepared orchestrator: $Out" -ForegroundColor Green
    Write-Host '[NEXT] Extract this ZIP and all eight prepared module .7z archives into one separate deployment directory.' -ForegroundColor Cyan
    Write-Host '[NEXT] Run LOCAL-LLM.bat install from that deployment directory, not from the source tree.' -ForegroundColor Cyan
} finally {
    if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
}
