[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$ApiKey = '',
    [int]$GpuIndex = -1
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Config = Get-Content -LiteralPath (Join-Path $Root 'config.json') -Raw | ConvertFrom-Json
if ($Port -eq 0) { $Port = [int]$Config.port }
if (-not $ApiKey) { $ApiKey = [string]$Config.api_key }
$Runtime = Join-Path $Root '_runtime'
$Python = Join-Path $Runtime 'python\python.exe'
$Downloads = Join-Path $Root '_download'
$Logs = Join-Path $Root 'logs'
$Models = Join-Path $Root 'models'
$RuntimeMarker = Join-Path $Runtime '.installed'
$RuntimeVersion = 'image-editor portable runtime v2 faceid-plusv2 torch-2.5.1-cu118'

function Initialize-PortableEnvironment {
    $PortableProfile = Join-Path $Root '_profile'
    $PortableTemp = Join-Path $Root '_temp'
    New-Item -ItemType Directory -Path $PortableProfile,$PortableTemp,$Logs,$Models,$Downloads -Force | Out-Null
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
    $env:PYTHONPYCACHEPREFIX = Join-Path $Root '_cache\pyc'
    $env:HF_HOME = Join-Path $Models 'huggingface'
    $env:HF_HUB_CACHE = Join-Path $Models 'huggingface\hub'
    $env:TRANSFORMERS_CACHE = $env:HF_HUB_CACHE
    $env:TORCH_HOME = Join-Path $Models 'torch'
    $env:XDG_CACHE_HOME = Join-Path $Root '_cache'
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
    $env:PATH = (Join-Path $Runtime 'vc') + ';' + (Split-Path $Python -Parent) + ';' + $env:SystemRoot + '\System32'
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

function Install-ImageEditor {
    Write-Host 'Preparing portable image editor. The first run downloads several large files.' -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $Runtime,$Models -Force | Out-Null
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

    Write-Host 'Installing CUDA PyTorch and API dependencies...' -ForegroundColor Cyan
    & $Uv pip install --python $Python --link-mode copy --index-url 'https://download.pytorch.org/whl/cu118' 'torch==2.5.1+cu118'
    if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed.' }
    & $Uv pip install --python $Python --link-mode copy --requirements (Join-Path $Root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Image editor Python package installation failed.' }

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

    $SmokeCode = "import torch,diffusers,transformers,accelerate,fastapi,insightface,onnxruntime,cv2; assert torch.cuda.is_available(), 'CUDA is not available'; print(torch.__version__, torch.cuda.get_device_name(0))"
    & $Python -c $SmokeCode
    if ($LASTEXITCODE -ne 0) { throw 'Installed runtime cannot use the NVIDIA GPU.' }
    Set-Content -LiteralPath $RuntimeMarker -Value $RuntimeVersion -Encoding ASCII
    Remove-Item -LiteralPath $Downloads -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root '_cache') -Recurse -Force -ErrorAction SilentlyContinue
}

function Wait-Healthy([string]$Url,[Diagnostics.Process]$Process) {
    $Deadline = [DateTime]::UtcNow.AddMinutes(45)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Process.HasExited) { throw "Image editor exited with code $($Process.ExitCode)." }
        try {
            $Response = Invoke-RestMethod -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($Response.status -eq 'ready') { return $Response }
            if ($Response.status -eq 'error') { throw "Model loading failed: $($Response.error)" }
        } catch {
            if ($_.Exception.Message -like 'Model loading failed:*') { throw }
        }
        Start-Sleep -Seconds 2
    }
    throw "Image editor did not become ready at $Url within 45 minutes."
}

function Quote-Argument([object]$Value) {
    $Text = [string]$Value
    if ($Text.Length -eq 0) { return '""' }
    if ($Text -notmatch '[\s"]') { return $Text }
    return '"' + $Text.Replace('"','\"') + '"'
}

Initialize-PortableEnvironment
$InstalledVersion = if (Test-Path -LiteralPath $RuntimeMarker -PathType Leaf) { (Get-Content -LiteralPath $RuntimeMarker -Raw).Trim() } else { '' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or $InstalledVersion -ne $RuntimeVersion) {
    Install-ImageEditor
    Initialize-PortableEnvironment
}

$Arguments = @((Join-Path $Root 'app.py'),'--host',[string]$Config.host,'--port',$Port,'--api-key',$ApiKey)
if ($GpuIndex -ge 0) { $Arguments += @('--gpu-index',$GpuIndex) }
$ArgumentLine = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
$Process = $null
try {
    Write-Host 'Starting image editor. Model download and generation progress will appear below.' -ForegroundColor Cyan
    $Process = Start-Process -FilePath $Python -ArgumentList $ArgumentLine -WorkingDirectory $Root -NoNewWindow -PassThru
    $ClientHost = if ([string]$Config.host -eq '::1') { '[::1]' } else { [string]$Config.host }
    $Url = "http://${ClientHost}:$Port"
    $Health = Wait-Healthy "$Url/health" $Process
    [ordered]@{
        schema = 1
        status = 'running'
        url = $Url
        edit_endpoint = "$Url/v1/images/edits"
        batch_edit_endpoint = "$Url/v1/images/edits/batch"
        api_key = $ApiKey
        gpu_indices = @($Health.devices | ForEach-Object { $_.gpu_index })
        model = [string]$Health.model
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'connection.json') -Encoding UTF8
    $ActiveGpuIndices = @($Health.devices | ForEach-Object { $_.gpu_index }) -join ','
    Write-Host "[READY] Image editor: $Url (GPU: $ActiveGpuIndices; $($Health.device_name))" -ForegroundColor Green
    Read-Host 'Press Enter to stop image editor' | Out-Null
} finally {
    if ($Process -and -not $Process.HasExited) {
        Write-Host 'Stopping image editor...'
        $Process.Kill()
        $Process.WaitForExit(10000) | Out-Null
    }
    if ($Process) { $Process.Dispose() }
}
