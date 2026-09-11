[CmdletBinding()]
param([switch]$CheckOnly, [switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$ModuleRoot = Split-Path -Parent $PSScriptRoot
$Manifest = Get-Content -LiteralPath (Join-Path $ModuleRoot 'module.json') -Raw | ConvertFrom-Json
$SourceRoot = Join-Path $ModuleRoot '_src'
$BuildRoot = Join-Path $ModuleRoot '_build'
$PayloadRoot = Join-Path $BuildRoot 'payload'
$PackageRoot = Join-Path $BuildRoot 'package'
$PreparedRoot = Join-Path $ModuleRoot 'prepared'

function Require-Source([string]$Name,[string]$Url) {
    $Path = Join-Path $SourceRoot $Name
    while (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-Host "[MISSING] $Name" -ForegroundColor Yellow
        Write-Host "[URL]     $Url"
        Write-Host "[PUT]     $Path"
        if ($NonInteractive -or $CheckOnly) { throw "Required source is missing: $Name" }
        Read-Host 'Put the unchanged file at the path above, then press Enter' | Out-Null
    }
    return $Path
}
function Reset-Directory([string]$Path) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}
function Expand-Artifact([string]$Archive,[string]$Destination,[bool]$Strip,[string]$SevenZip) {
    $Stage = Join-Path $BuildRoot ('extract-' + [guid]::NewGuid().ToString('N'))
    $Content = Join-Path $Stage 'content'
    New-Item -ItemType Directory -Path $Content -Force | Out-Null
    try {
        if ($Archive.EndsWith('.tar.gz',[StringComparison]::OrdinalIgnoreCase)) {
            $Gzip = Join-Path $Stage 'gzip'; New-Item -ItemType Directory -Path $Gzip | Out-Null
            & $SevenZip x -y "-o$Gzip" $Archive | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Cannot expand gzip: $Archive" }
            $Tar = Get-ChildItem -LiteralPath $Gzip -Filter '*.tar' -File | Select-Object -First 1
            if (-not $Tar) { throw "No tar member in $Archive" }
            & $SevenZip x -y "-o$Content" $Tar.FullName | Out-Null
        } else { & $SevenZip x -y "-o$Content" $Archive | Out-Null }
        if ($LASTEXITCODE -ne 0) { throw "Cannot expand: $Archive" }
        $CopyRoot = $Content
        if ($Strip) {
            $Items = @(Get-ChildItem -LiteralPath $Content -Force)
            if ($Items.Count -eq 1 -and $Items[0].PSIsContainer) { $CopyRoot = $Items[0].FullName }
        }
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        Copy-Item -Path (Join-Path $CopyRoot '*') -Destination $Destination -Recurse -Force
    } finally { if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force } }
}

if ($ModuleRoot.IndexOfAny([char[]]'!%&^<>|') -ge 0) { throw "Unsafe build path: $ModuleRoot" }
New-Item -ItemType Directory -Path $SourceRoot,$PreparedRoot -Force | Out-Null
$OnlineRoot=Join-Path $SourceRoot '_online';$OnlineProfile=Join-Path $OnlineRoot 'profile';$OnlineTemp=Join-Path $OnlineRoot 'temp';New-Item -ItemType Directory -Path $OnlineProfile,$OnlineTemp -Force|Out-Null
$env:HOME=$OnlineProfile;$env:USERPROFILE=$OnlineProfile;$env:APPDATA=Join-Path $OnlineProfile 'AppData\Roaming';$env:LOCALAPPDATA=Join-Path $OnlineProfile 'AppData\Local';$env:TEMP=$OnlineTemp;$env:TMP=$OnlineTemp;$env:PSModuleAnalysisCachePath=Join-Path $OnlineRoot 'powershell\ModuleAnalysisCache';$env:DOTNET_CLI_HOME=$OnlineProfile;$env:NUGET_PACKAGES=Join-Path $OnlineRoot 'nuget';$env:PIP_CACHE_DIR=Join-Path $OnlineRoot 'pip';$env:UV_CACHE_DIR=Join-Path $OnlineRoot 'uv';$env:npm_config_cache=Join-Path $OnlineRoot 'npm';$env:GIT_CONFIG_GLOBAL=Join-Path $OnlineRoot 'gitconfig';$env:GIT_CONFIG_NOSYSTEM='1';$env:PYTHONNOUSERSITE='1';$env:PYTHONPYCACHEPREFIX=Join-Path $OnlineRoot 'python-bytecode'
$SevenZipVersion='26.02'; $SevenZipTag='2602'
$Bootstrap=Require-Source '7zr.exe' "https://github.com/ip7z/7zip/releases/download/$SevenZipVersion/7zr.exe"
$Extra=Require-Source "7z$SevenZipTag-extra.7z" "https://github.com/ip7z/7zip/releases/download/$SevenZipVersion/7z$SevenZipTag-extra.7z"
foreach($Artifact in $Manifest.artifacts){ Require-Source $Artifact.file $Artifact.url | Out-Null }
if($CheckOnly){ Write-Host "[OK] $($Manifest.name): fixed inputs are present"; return }

Reset-Directory $BuildRoot
$ToolRoot=Join-Path $BuildRoot 'tools'; New-Item -ItemType Directory -Path $ToolRoot | Out-Null
& $Bootstrap x -y "-o$ToolRoot" $Extra | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot bootstrap 7za.exe'}
$SevenZip=Join-Path $ToolRoot 'x64\7za.exe'
New-Item -ItemType Directory -Path $PayloadRoot | Out-Null
foreach($Artifact in $Manifest.artifacts){
    $Source=Join-Path $SourceRoot $Artifact.file; $Target=Join-Path $PayloadRoot $Artifact.target
    if($Artifact.kind -eq 'file'){New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force|Out-Null; Copy-Item -LiteralPath $Source -Destination $Target -Force}
    else{Expand-Artifact $Source $Target ([bool]$Artifact.strip_single_root) $SevenZip}
    if(-not(Test-Path -LiteralPath (Join-Path $PayloadRoot $Artifact.key) -PathType Leaf)){throw "Artifact key missing: $($Artifact.key)"}
}
$Hook=Join-Path $PSScriptRoot 'build-hook.ps1'
if(Test-Path -LiteralPath $Hook){& $Hook -ModuleRoot $ModuleRoot -PayloadRoot $PayloadRoot -SourceRoot $SourceRoot -SevenZip $SevenZip; if($LASTEXITCODE -ne 0){throw 'Build hook failed'}}

$Packaged=Join-Path $PackageRoot (Join-Path 'modules' $Manifest.name)
New-Item -ItemType Directory -Path $Packaged -Force|Out-Null
Copy-Item -Path (Join-Path $PayloadRoot '*') -Destination $Packaged -Recurse -Force
foreach($Name in @('MODULE.bat','control.ps1','module.json','README.md')){Copy-Item -LiteralPath (Join-Path $ModuleRoot $Name) -Destination $Packaged -Force}
Copy-Item -LiteralPath (Join-Path $ModuleRoot 'lib') -Destination (Join-Path $Packaged 'lib') -Recurse -Force
$Entries=@();$Mutable=@($Manifest.mutable_paths);Get-ChildItem -LiteralPath $Packaged -File -Recurse|Sort-Object FullName|ForEach-Object{$relative=$_.FullName.Substring($Packaged.Length+1).Replace('\','/');$skip=$false;foreach($prefix in $Mutable){$rule=[string]$prefix;if(($rule.EndsWith('/') -and $relative.StartsWith($rule,[StringComparison]::OrdinalIgnoreCase)) -or $relative.Equals($rule,[StringComparison]::OrdinalIgnoreCase)){$skip=$true}};if(-not $skip){$Entries += [ordered]@{path=$relative;bytes=$_.Length;sha256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}}}
[ordered]@{schema=1;module=$Manifest.name;version=$Manifest.version;files=$Entries}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $Packaged 'payload.sha256.json') -Encoding UTF8
& (Join-Path $Packaged 'control.ps1') -CommandName verify-payload
if($LASTEXITCODE -ne 0){throw 'Payload verification failed'}
foreach($relative in @('config\runtime','data','logs','state','temp')){$mutableRoot=Join-Path $Packaged $relative;if(Test-Path -LiteralPath $mutableRoot){Remove-Item -LiteralPath $mutableRoot -Recurse -Force}}
foreach($relative in @($Manifest.mutable_paths)){$mutableItem=Join-Path $Packaged ([string]$relative);if(Test-Path -LiteralPath $mutableItem){Remove-Item -LiteralPath $mutableItem -Recurse -Force}}
$Archive=Join-Path $PreparedRoot ("local-llm-{0}-{1}.7z" -f $Manifest.name,$Manifest.version)
if(Test-Path $Archive){Remove-Item $Archive -Force}
Push-Location $PackageRoot
try{& $SevenZip a -t7z -mx=7 -ms=on -mmt=on $Archive 'modules'|Out-Null;if($LASTEXITCODE -ne 0){throw 'Packaging failed'};& $SevenZip t $Archive|Out-Null;if($LASTEXITCODE -ne 0){throw 'Archive test failed'}}finally{Pop-Location}
$Rehydrate=Join-Path $BuildRoot 'rehydrate';New-Item -ItemType Directory -Path $Rehydrate -Force|Out-Null;& $SevenZip x -y "-o$Rehydrate" $Archive|Out-Null;if($LASTEXITCODE -ne 0){throw 'Archive rehydration failed'};& (Join-Path $Rehydrate "modules\$($Manifest.name)\control.ps1") -CommandName verify-payload;if($LASTEXITCODE -ne 0){throw 'Rehydrated payload verification failed'};Remove-Item $Rehydrate -Recurse -Force
Write-Host "[OK] Prepared: $Archive" -ForegroundColor Green
