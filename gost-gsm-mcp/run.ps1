[CmdletBinding()]
param(
    [Parameter(Position=0,ValueFromRemainingArguments=$true)]
    [string[]]$CommandArgs
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Runtime = Join-Path $Root '_runtime'
$Python = Join-Path $Runtime 'python\python.exe'
$Downloads = Join-Path $Root '_download'
$Marker = Join-Path $Runtime '.installed'
$RuntimeVersion = 'gost-gsm-mcp runtime v1; python 3.13; mcp 2.2'

function Write-Status([string]$Message) {
    [Console]::Error.WriteLine($Message)
}

function Initialize-PortableEnvironment {
    $PortableProfile = Join-Path $Root '_profile'
    $PortableTemp = Join-Path $Root '_temp'
    New-Item -ItemType Directory -Path $PortableProfile,$PortableTemp,$Downloads -Force | Out-Null
    $env:USERPROFILE = $PortableProfile
    $env:APPDATA = Join-Path $PortableProfile 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $PortableProfile 'AppData\Local'
    $env:TEMP = $PortableTemp
    $env:TMP = $PortableTemp
    $env:PIP_CACHE_DIR = Join-Path $Root '_cache\pip'
    $env:UV_CACHE_DIR = Join-Path $Root '_cache\uv'
    $env:UV_NO_CONFIG = '1'
    $env:UV_LINK_MODE = 'copy'
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONPYCACHEPREFIX = Join-Path $Root '_cache\pyc'
    $env:PYTHONPATH = $Root
    $env:PATH = (Join-Path $Runtime 'vc') + ';' + (Split-Path $Python -Parent) + ';' + $env:SystemRoot + '\System32'
    if (-not $env:GOST_GSM_PROJECT_ROOT) {
        $env:GOST_GSM_PROJECT_ROOT = Join-Path $Root 'jobs\default'
    }
}

function Download-File([string]$Name,[string]$Url) {
    $Destination = Join-Path $Downloads $Name
    if (Test-Path -LiteralPath $Destination -PathType Leaf) { return $Destination }
    $Partial = "$Destination.partial"
    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    Write-Status "Downloading $Name..."
    $OldProgress = $ProgressPreference
    try {
        $ProgressPreference = 'SilentlyContinue'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Partial
        Move-Item -LiteralPath $Partial -Destination $Destination -Force
    } finally {
        $ProgressPreference = $OldProgress
        Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    }
    return $Destination
}

function Get-7Zip {
    $Tools = Join-Path $Runtime 'tools'
    $SevenZip = Join-Path $Tools 'x64\7za.exe'
    if (Test-Path -LiteralPath $SevenZip -PathType Leaf) { return $SevenZip }
    New-Item -ItemType Directory -Path $Tools -Force | Out-Null
    $Bootstrap = Download-File '7zr.exe' 'https://github.com/ip7z/7zip/releases/download/26.02/7zr.exe'
    $Extra = Download-File '7z2602-extra.7z' 'https://github.com/ip7z/7zip/releases/download/26.02/7z2602-extra.7z'
    & $Bootstrap x -y "-o$Tools" $Extra | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $SevenZip -PathType Leaf)) {
        throw 'Cannot prepare portable 7-Zip.'
    }
    return $SevenZip
}

function Expand-PortableArchive([string]$Archive,[string]$Destination,[bool]$StripRoot,[string]$SevenZip) {
    $Stage = Join-Path $Root ('_extract-' + [guid]::NewGuid().ToString('N'))
    $Content = Join-Path $Stage 'content'
    New-Item -ItemType Directory -Path $Content -Force | Out-Null
    try {
        if ($Archive.EndsWith('.tar.gz',[StringComparison]::OrdinalIgnoreCase)) {
            $Gzip = Join-Path $Stage 'gzip'
            New-Item -ItemType Directory -Path $Gzip | Out-Null
            & $SevenZip x -y "-o$Gzip" $Archive | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Cannot unpack $Archive" }
            $Tar = Get-ChildItem -LiteralPath $Gzip -Filter '*.tar' -File | Select-Object -First 1
            if (-not $Tar) { throw "No tar member in $Archive" }
            & $SevenZip x -y "-o$Content" $Tar.FullName | Out-Null
        } else {
            & $SevenZip x -y "-o$Content" $Archive | Out-Null
        }
        if ($LASTEXITCODE -ne 0) { throw "Cannot unpack $Archive" }
        $CopyRoot = $Content
        if ($StripRoot) {
            $Items = @(Get-ChildItem -LiteralPath $Content -Force)
            if ($Items.Count -eq 1 -and $Items[0].PSIsContainer) { $CopyRoot = $Items[0].FullName }
        }
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        Copy-Item -Path (Join-Path $CopyRoot '*') -Destination $Destination -Recurse -Force
    } finally {
        Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Install-Runtime {
    Write-Status 'Preparing portable Python for GOST GSM MCP...'
    New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
    $SevenZip = Get-7Zip
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $Archive = Download-File 'cpython-3.13.15+20260901-x86_64-pc-windows-msvc-install_only.tar.gz' 'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.13.15+20260901-x86_64-pc-windows-msvc-install_only.tar.gz'
        Expand-PortableArchive $Archive (Join-Path $Runtime 'python') $true $SevenZip
    }
    $Uv = Join-Path $Runtime 'uv\uv.exe'
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
        $Archive = Download-File 'uv-x86_64-pc-windows-msvc.zip' 'https://github.com/astral-sh/uv/releases/download/0.12.9/uv-x86_64-pc-windows-msvc.zip'
        Expand-PortableArchive $Archive (Join-Path $Runtime 'uv') $false $SevenZip
    }
    & $Uv pip install --python $Python --link-mode copy --requirements (Join-Path $Root 'requirements.txt') |
        ForEach-Object { [Console]::Error.WriteLine($_) }
    if ($LASTEXITCODE -ne 0) { throw 'GOST GSM MCP package installation failed.' }

    $VcRoot = Join-Path $Runtime 'vc'
    if (-not (Test-Path -LiteralPath (Join-Path $VcRoot 'vcruntime140.dll') -PathType Leaf)) {
        $Vc = Download-File 'VC_redist.x64.exe' 'https://aka.ms/vs/17/release/VC_redist.x64.exe'
        $Stage = Join-Path $Root ('_vc-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path "$Stage\parts","$Stage\payload","$Stage\dlls",$VcRoot -Force | Out-Null
        try {
            & $SevenZip x -y -t# "-o$Stage\parts" $Vc | Out-Null
            & $SevenZip x -y "-o$Stage\payload" "$Stage\parts\4.cab" | Out-Null
            & $SevenZip x -y "-o$Stage\dlls" "$Stage\payload\a12" | Out-Null
            Get-ChildItem -LiteralPath "$Stage\dlls" -Filter '*_amd64' -File | ForEach-Object {
                $Name = $_.Name.Replace('_amd64','')
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $VcRoot $Name) -Force
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path (Split-Path $Python -Parent) $Name) -Force
            }
        } finally {
            Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    & $Python (Join-Path $Root 'server.py') --self-check |
        ForEach-Object { [Console]::Error.WriteLine($_) }
    if ($LASTEXITCODE -ne 0) { throw 'Installed GOST GSM MCP runtime failed its self-check.' }
    Set-Content -LiteralPath $Marker -Value $RuntimeVersion -Encoding ASCII
    Remove-Item -LiteralPath $Downloads -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root '_cache') -Recurse -Force -ErrorAction SilentlyContinue
}

Initialize-PortableEnvironment
$InstalledVersion = if (Test-Path -LiteralPath $Marker -PathType Leaf) { (Get-Content -LiteralPath $Marker -Raw).Trim() } else { '' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or $InstalledVersion -ne $RuntimeVersion) {
    Install-Runtime
    Initialize-PortableEnvironment
}

if ($CommandArgs.Count -and $CommandArgs[0] -eq '--python') {
    $PythonArgs = @($CommandArgs | Select-Object -Skip 1)
    & $Python @PythonArgs
} elseif ($CommandArgs.Count -and $CommandArgs[0] -eq '--test') {
    & $Python -m unittest discover -s (Join-Path $Root 'tests') -v
} else {
    & $Python (Join-Path $Root 'server.py')
}
exit $LASTEXITCODE
