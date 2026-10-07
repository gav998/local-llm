[CmdletBinding()]
param(
    [ValidateRange(1,65535)]
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Runtime = Join-Path $Root '_runtime'
$Python = Join-Path $Runtime 'python\python.exe'
$OpenWebUiModule = Join-Path $Runtime 'python\Lib\site-packages\open_webui\__init__.py'
$Downloads = Join-Path $Root '_download'
$Logs = Join-Path $Root 'logs'
$Data = Join-Path $Root 'data'
$RuntimeMarker = Join-Path $Runtime '.installed'
$RuntimeVersion = 'Open WebUI portable runtime v1 / Python 3.11'

function Initialize-PortableEnvironment {
    $PortableProfile = Join-Path $Root '_profile'
    $PortableTemp = Join-Path $Root '_temp'
    $Cache = Join-Path $Root '_cache'
    $PortableDesktop = Join-Path $PortableProfile 'Desktop'
    New-Item -ItemType Directory -Path $PortableProfile,$PortableDesktop,$PortableTemp,$Cache,$Logs,$Data,$Downloads -Force | Out-Null
    $env:USERPROFILE = $PortableProfile
    $env:APPDATA = Join-Path $PortableProfile 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $PortableProfile 'AppData\Local'
    $env:TEMP = $PortableTemp
    $env:TMP = $PortableTemp
    $env:PIP_CACHE_DIR = Join-Path $Cache 'pip'
    $env:UV_CACHE_DIR = Join-Path $Cache 'uv'
    $env:UV_NO_CONFIG = '1'
    $env:UV_LINK_MODE = 'copy'
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONPYCACHEPREFIX = Join-Path $Cache 'pyc'
    $env:XDG_CACHE_HOME = Join-Path $Cache 'xdg'
    $env:HF_HOME = Join-Path $Cache 'huggingface'
    $env:HUGGINGFACE_HUB_CACHE = Join-Path $Cache 'huggingface\hub'
    $env:SENTENCE_TRANSFORMERS_HOME = Join-Path $Cache 'sentence-transformers'
    $env:TORCH_HOME = Join-Path $Cache 'torch'
    $env:NLTK_DATA = Join-Path $Data 'nltk_data'
    $env:DATA_DIR = $Data
    $env:UVICORN_WORKERS = '1'
    $env:PATH = (Join-Path $Runtime 'vc') + ';' + (Split-Path $Python -Parent) + ';' + (Join-Path $Runtime 'python\Scripts') + ';' + $env:SystemRoot + '\System32'
}

function Download-File([string]$Name,[string]$Url) {
    $Destination = Join-Path $Downloads $Name
    if (Test-Path -LiteralPath $Destination -PathType Leaf) { return $Destination }
    $Partial = "$Destination.partial"
    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    Write-Host "Downloading $Name..." -ForegroundColor Cyan
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
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $SevenZip -PathType Leaf)) { throw 'Cannot prepare portable 7-Zip.' }
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

function Install-OpenWebUi {
    Write-Host 'Preparing portable Python 3.11 and Open WebUI. The first run can take several minutes.' -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
    $SevenZip = Get-7Zip
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $Archive = Download-File 'cpython-3.11.16+20260901-x86_64-pc-windows-msvc-install_only.tar.gz' 'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.11.16+20260901-x86_64-pc-windows-msvc-install_only.tar.gz'
        Expand-PortableArchive $Archive (Join-Path $Runtime 'python') $true $SevenZip
    }
    $Uv = Join-Path $Runtime 'uv\uv.exe'
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
        $Archive = Download-File 'uv-x86_64-pc-windows-msvc.zip' 'https://github.com/astral-sh/uv/releases/download/0.12.9/uv-x86_64-pc-windows-msvc.zip'
        Expand-PortableArchive $Archive (Join-Path $Runtime 'uv') $false $SevenZip
    }
    Write-Host 'Installing Open WebUI...'
    & $Uv pip install --python $Python --link-mode copy open-webui
    if ($LASTEXITCODE -ne 0) { throw 'Open WebUI Python package installation failed.' }

    $VcRoot = Join-Path $Runtime 'vc'
    if (-not (Test-Path -LiteralPath (Join-Path $VcRoot 'vcruntime140.dll') -PathType Leaf)) {
        $Vc = Download-File 'VC_redist.x64.exe' 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
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
    if (-not (Test-Path -LiteralPath $OpenWebUiModule -PathType Leaf)) { throw 'Open WebUI Python package was not installed.' }
    & $Python -c "import importlib.metadata; print('Open WebUI ' + importlib.metadata.version('open-webui') + ' runtime OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Installed Open WebUI runtime cannot be loaded.' }
    Set-Content -LiteralPath $RuntimeMarker -Value $RuntimeVersion -Encoding ASCII
    Remove-Item -LiteralPath $Downloads -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root '_cache') -Recurse -Force -ErrorAction SilentlyContinue
}

function Wait-Healthy([string]$Url,[Diagnostics.Process]$Process) {
    $Deadline = [DateTime]::UtcNow.AddMinutes(15)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Process.HasExited) { throw "Open WebUI exited with code $($Process.ExitCode). See logs/." }
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri "$Url/health" -TimeoutSec 3
            if ($Response.StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "Open WebUI did not become ready at $Url. See logs/."
}

Initialize-PortableEnvironment
$InstalledVersion = if (Test-Path -LiteralPath $RuntimeMarker -PathType Leaf) { (Get-Content -LiteralPath $RuntimeMarker -Raw).Trim() } else { '' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or -not (Test-Path -LiteralPath $OpenWebUiModule -PathType Leaf) -or $InstalledVersion -ne $RuntimeVersion) {
    Install-OpenWebUi
    Initialize-PortableEnvironment
}

$Url = "http://127.0.0.1:$Port"
$OutLog = Join-Path $Logs 'webui.out.log'
$ErrorLog = Join-Path $Logs 'webui.error.log'
Remove-Item -LiteralPath $OutLog,$ErrorLog -Force -ErrorAction SilentlyContinue
$Process = $null
try {
    # uv-generated console launchers contain the absolute Python path from install
    # time. Invoke Open WebUI with the portable interpreter so a moved folder
    # continues to work without reinstalling the runtime. Keep this expression
    # free of spaces because Windows PowerShell joins ArgumentList items.
    $LaunchCode = "__import__('open_webui').serve(host='127.0.0.1',port=$Port)"
    $Process = Start-Process -FilePath $Python -ArgumentList @('-c',$LaunchCode) -WorkingDirectory $Data -NoNewWindow -RedirectStandardOutput $OutLog -RedirectStandardError $ErrorLog -PassThru
    Wait-Healthy $Url $Process
    Write-Host "[READY] Open WebUI: $Url" -ForegroundColor Green
    Read-Host 'Press Enter to stop Open WebUI' | Out-Null
} finally {
    if ($Process -and -not $Process.HasExited) {
        Write-Host 'Stopping Open WebUI...'
        $Process.Kill()
        $Process.WaitForExit(10000) | Out-Null
    }
    if ($Process) { $Process.Dispose() }
}
