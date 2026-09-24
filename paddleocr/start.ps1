[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$ApiKey = '',
    [string]$Device = ''
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Config = Get-Content -LiteralPath (Join-Path $Root 'config.json') -Raw | ConvertFrom-Json
if ($Port -eq 0) { $Port = [int]$Config.port }
if (-not $ApiKey) { $ApiKey = [string]$Config.api_key }
if (-not $Device) { $Device = [string]$Config.device }
$Runtime = Join-Path $Root '_runtime'
$Python = Join-Path $Runtime 'python\python.exe'
$Models = Join-Path $Root 'models'
$Downloads = Join-Path $Root '_download'
$Logs = Join-Path $Root 'logs'
$Jobs = Join-Path $Root 'data\jobs'

function Initialize-PortableEnvironment {
    $PortableProfile = Join-Path $Root '_profile'
    $PortableTemp = Join-Path $Root '_temp'
    New-Item -ItemType Directory -Path $PortableProfile,$PortableTemp,$Logs,$Jobs,$Downloads -Force | Out-Null
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
    $env:PADDLE_PDX_CACHE_HOME = $Models
    $env:PADDLE_PDX_DISABLE_DEVICE_FALLBACK = '1'
    $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = '1'
    $env:LOCAL_OCR_DEVICE = $Device
    $env:LOCAL_OCR_PREFER_GPU_INDEX = [string]$Config.prefer_gpu
    $env:LOCAL_OCR_TEXT_REC_BATCH_SIZE = [string]$Config.text_recognition_batch_size
    $env:LOCAL_OCR_MAX_BLOCK_TOKENS = [string]$Config.max_block_tokens
    $env:LOCAL_OCR_TOKEN = $ApiKey
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

function Test-Model([string]$Name) {
    $Required = if ($Name -eq 'PP-Chart2Table') {
        @('config.json','inference.yml','model_state.pdparams')
    } else {
        @('inference.json','inference.yml','inference.pdiparams')
    }
    foreach ($File in $Required) {
        if (-not (Test-Path -LiteralPath (Join-Path (Join-Path $Models $Name) $File) -PathType Leaf)) { return $false }
    }
    return $true
}

function Test-Installed {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $Runtime '.installed') -PathType Leaf)) { return $false }
    foreach ($Name in @('PP-DocLayout-L','PP-DocBlockLayout','PP-OCRv6_medium_det','eslav_PP-OCRv5_mobile_rec','SLANet_plus','PP-LCNet_x1_0_doc_ori','UVDoc','PP-LCNet_x1_0_textline_ori','PP-OCRv4_server_seal_det','PP-FormulaNet_plus-S','PP-Chart2Table')) {
        if (-not (Test-Model $Name)) { return $false }
    }
    return $true
}

function Install-PaddleOcr {
    Write-Host 'Preparing portable PaddleOCR. The first run downloads several large files.' -ForegroundColor Cyan
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

    $ModelAssets = @(
        @('PP-DocLayout-L','PP-DocLayout-L_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocLayout-L_infer.tar'),
        @('PP-DocBlockLayout','PP-DocBlockLayout_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocBlockLayout_infer.tar'),
        @('PP-OCRv6_medium_det','PP-OCRv6_medium_det_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv6_medium_det_infer.tar'),
        @('eslav_PP-OCRv5_mobile_rec','eslav_PP-OCRv5_mobile_rec_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/eslav_PP-OCRv5_mobile_rec_infer.tar'),
        @('SLANet_plus','SLANet_plus_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/SLANet_plus_infer.tar'),
        @('PP-LCNet_x1_0_doc_ori','PP-LCNet_x1_0_doc_ori_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-LCNet_x1_0_doc_ori_infer.tar'),
        @('UVDoc','UVDoc_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/UVDoc_infer.tar'),
        @('PP-LCNet_x1_0_textline_ori','PP-LCNet_x1_0_textline_ori_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-LCNet_x1_0_textline_ori_infer.tar'),
        @('PP-OCRv4_server_seal_det','PP-OCRv4_server_seal_det_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv4_server_seal_det_infer.tar'),
        @('PP-FormulaNet_plus-S','PP-FormulaNet_plus-S_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-FormulaNet_plus-S_infer.tar'),
        @('PP-Chart2Table','PP-Chart2Table_infer.tar','https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-Chart2Table_infer.tar')
    )
    foreach ($Asset in $ModelAssets) {
        $Target = Join-Path $Models $Asset[0]
        if (-not (Test-Model $Asset[0])) {
            $Archive = Download-File $Asset[1] $Asset[2]
            Expand-PortableArchive $Archive $Target $true $SevenZip
            if (-not (Test-Model $Asset[0])) { throw "Incomplete PaddleOCR model: $($Asset[0])" }
        }
    }

    $PaddleWheel = Download-File 'paddlepaddle_gpu-3.3.1-cp311-cp311-win_amd64.whl' 'https://paddle-whl.cdn.bcebos.com/stable/cu118/paddlepaddle-gpu/paddlepaddle_gpu-3.3.1-cp311-cp311-win_amd64.whl'
    Write-Host 'Installing Python packages...'
    & $Uv pip install --python $Python --link-mode copy $PaddleWheel --requirements (Join-Path $Root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'PaddleOCR Python package installation failed.' }

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

    & $Python -c 'import paddle,paddleocr,paddlex,pymupdf; print("PaddleOCR runtime OK")'
    if ($LASTEXITCODE -ne 0) { throw 'Installed PaddleOCR runtime cannot be imported.' }
    Set-Content -LiteralPath (Join-Path $Runtime '.installed') -Value 'PaddleOCR 3.7.0 / PaddlePaddle GPU 3.3.1' -Encoding ASCII
    Remove-Item -LiteralPath $Downloads -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root '_cache') -Recurse -Force -ErrorAction SilentlyContinue
}

function Wait-Healthy([string]$Url,[Diagnostics.Process]$Process) {
    $Deadline = [DateTime]::UtcNow.AddMinutes(15)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Process.HasExited) { throw "PaddleOCR exited with code $($Process.ExitCode). See logs/." }
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($Response.StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "PaddleOCR did not become ready at $Url. See logs/."
}

function Quote-Argument([object]$Value) {
    $Text = [string]$Value
    if ($Text.Length -eq 0) { return '""' }
    if ($Text -notmatch '[\s"]') { return $Text }
    return '"' + $Text.Replace('"','\"') + '"'
}

Initialize-PortableEnvironment
if (-not (Test-Installed)) {
    Install-PaddleOcr
    Initialize-PortableEnvironment
}

$Connection = [ordered]@{ schema=1; url="http://$($Config.host):$Port"; api_key=$ApiKey }
$Connection | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'connection.json') -Encoding UTF8
$Arguments = @((Join-Path $Root 'server.py'),'--config',(Join-Path $Root 'pipeline.yaml'),'--model-root',$Models,'--jobs-root',$Jobs,'--host',$Config.host,'--port',$Port,'--token',$ApiKey)
$ArgumentLine = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
$OutLog = Join-Path $Logs 'server.out.log'
$ErrorLog = Join-Path $Logs 'server.error.log'
Remove-Item -LiteralPath $OutLog,$ErrorLog -Force -ErrorAction SilentlyContinue
$Process = $null
try {
    Write-Host "Starting PaddleOCR on GPU setting '$Device'. Initial model loading can take several minutes."
    $Process = Start-Process -FilePath $Python -ArgumentList $ArgumentLine -WorkingDirectory $Root -NoNewWindow -RedirectStandardOutput $OutLog -RedirectStandardError $ErrorLog -PassThru
    Wait-Healthy "http://$($Config.host):$Port/health" $Process
    Write-Host "[READY] PaddleOCR: http://$($Config.host):$Port" -ForegroundColor Green
    Write-Host "Simple API: POST /v1/ocr (Bearer $ApiKey, multipart field: file)"
    Read-Host 'Press Enter to stop PaddleOCR' | Out-Null
} finally {
    if ($Process -and -not $Process.HasExited) {
        Write-Host 'Stopping PaddleOCR...'
        $Process.Kill()
        $Process.WaitForExit(10000) | Out-Null
    }
    if ($Process) { $Process.Dispose() }
}
