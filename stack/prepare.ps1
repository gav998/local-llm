$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $PSScriptRoot '_build\package'
$PreparedRoot = Join-Path $Root 'prepared'
$Out = Join-Path $PreparedRoot 'local-llm-stack-2026.09.16.zip'
$ModuleNames = @('mysql','elasticsearch','silo','valkey','llama-cpp','paddleocr','ragflow','web')
$SevenZipVersion = '26.02'
$SevenZipTag = '2602'

$MissingArchives = @()
$ArchiveEntries = @()
foreach ($Name in $ModuleNames) {
    $ModuleRoot = Join-Path (Join-Path $Root 'modules') $Name
    $ManifestPath = Join-Path $ModuleRoot 'module.json'
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Module manifest is missing: $ManifestPath"
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $Archive = Join-Path $PreparedRoot ("local-llm-{0}-{1}.7z" -f $Manifest.name,$Manifest.version)
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf) -or (Get-Item -LiteralPath $Archive).Length -eq 0) {
        Write-Host "[MISSING] $Archive" -ForegroundColor Yellow
        $MissingArchives += $Name
    } else {
        $ArchiveEntries += [ordered]@{
            name = $Name
            file = (Get-Item -LiteralPath $Archive).Name
        }
    }
}
if ($MissingArchives.Count -gt 0) {
    throw "Module archives are not prepared: $($MissingArchives -join ', '). CHECK-SOURCES.bat checks downloads only; run 1.PREPARE-ONLINE.bat and prepare every missing module, then run PREPARE-STACK.bat again."
}

$SevenZip = Join-Path $Root 'modules\mysql\_build\tools\x64\7za.exe'
$SevenZipLicense = Join-Path $Root 'modules\mysql\_build\tools\License.txt'
if (-not (Test-Path -LiteralPath $SevenZip -PathType Leaf)) {
    $Bootstrap = Join-Path $Root '_src\7zr.exe'
    $Extra = Join-Path $Root "_src\7z$SevenZipTag-extra.7z"
    if (-not (Test-Path -LiteralPath $Bootstrap -PathType Leaf) -or -not (Test-Path -LiteralPath $Extra -PathType Leaf)) {
        throw "Cannot bundle the matching 7-Zip extractor. Expected $SevenZip or the pinned bootstrap inputs $Bootstrap and $Extra."
    }
    $ToolRoot = Join-Path $PSScriptRoot '_build\sevenzip'
    if (Test-Path -LiteralPath $ToolRoot) { Remove-Item -LiteralPath $ToolRoot -Recurse -Force }
    New-Item -ItemType Directory -Path $ToolRoot -Force | Out-Null
    & $Bootstrap x -y "-o$ToolRoot" $Extra
    if ($LASTEXITCODE -ne 0) { throw 'Cannot bootstrap the deployment 7za.exe' }
    $SevenZip = Join-Path $ToolRoot 'x64\7za.exe'
    $SevenZipLicense = Join-Path $ToolRoot 'License.txt'
}
if (-not (Test-Path -LiteralPath $SevenZip -PathType Leaf)) { throw "7za.exe is missing: $SevenZip" }
if (-not (Test-Path -LiteralPath $SevenZipLicense -PathType Leaf)) { throw "7-Zip license is missing: $SevenZipLicense" }
$SevenZipInfo = (& $SevenZip i | Out-String)
if ($LASTEXITCODE -ne 0 -or $SevenZipInfo -notmatch [regex]::Escape($SevenZipVersion)) {
    throw "Expected 7-Zip $SevenZipVersion deployment extractor: $SevenZip"
}
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage,(Split-Path $Out) -Force | Out-Null
try {
    Copy-Item -LiteralPath (Join-Path $Root 'LOCAL-LLM.bat'),(Join-Path $Root 'EXTRACT-MODULES.bat'),(Join-Path $Root 'README.md'),(Join-Path $Root 'LICENSE') -Destination $Stage
    $StackStage = Join-Path $Stage 'stack'
    New-Item -ItemType Directory -Path $StackStage | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'control.ps1'),(Join-Path $PSScriptRoot 'extract.ps1'),(Join-Path $PSScriptRoot 'README.md') -Destination $StackStage
    [ordered]@{schema=1;archives=$ArchiveEntries}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath (Join-Path $StackStage 'module-archives.json') -Encoding UTF8
    $ToolsStage = Join-Path $Stage 'tools'
    New-Item -ItemType Directory -Path $ToolsStage | Out-Null
    Copy-Item -LiteralPath $SevenZip -Destination (Join-Path $ToolsStage '7za.exe')
    Copy-Item -LiteralPath $SevenZipLicense -Destination (Join-Path $ToolsStage '7zip-LICENSE.txt')
    if (Test-Path -LiteralPath $Out) { Remove-Item -LiteralPath $Out -Force }
    Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath $Out -CompressionLevel Optimal
    Write-Host "[OK] Prepared orchestrator: $Out" -ForegroundColor Green
    Write-Host '[NEXT] Put this ZIP and all eight module .7z files in one deployment directory and extract only this ZIP there.' -ForegroundColor Cyan
    Write-Host '[NEXT] Run EXTRACT-MODULES.bat, then LOCAL-LLM.bat install.' -ForegroundColor Cyan
} finally {
    if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
}
