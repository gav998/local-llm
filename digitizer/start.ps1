[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$PaddleUrl = '',
    [string]$PaddleKey = '',
    [string]$LlamaUrl = '',
    [string]$LlamaKey = ''
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Config = Get-Content -LiteralPath (Join-Path $Root 'config.json') -Raw | ConvertFrom-Json
if ($Port -eq 0) { $Port = [int]$Config.port }
$Runtime = Join-Path $Root '_runtime'
$Python = Join-Path $Runtime 'python\python.exe'
$Downloads = Join-Path $Root '_download'
$Logs = Join-Path $Root 'logs'
$Data = Join-Path $Root 'data'

function Initialize-PortableEnvironment {
    $PortableProfile = Join-Path $Root '_profile'
    $PortableTemp = Join-Path $Root '_temp'
    New-Item -ItemType Directory -Path $PortableProfile,$PortableTemp,$Logs,$Data,$Downloads -Force | Out-Null
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

function Install-Digitizer {
    Write-Host 'Preparing portable embedded Python for digitizer...' -ForegroundColor Cyan
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
    & $Uv pip install --python $Python --link-mode copy --requirements (Join-Path $Root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Digitizer Python package installation failed.' }

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
    & $Python -c 'import fastapi,httpx,pymupdf,uvicorn; print("Digitizer runtime OK")'
    if ($LASTEXITCODE -ne 0) { throw 'Installed digitizer runtime cannot be imported.' }
    Set-Content -LiteralPath (Join-Path $Runtime '.installed') -Value 'digitizer portable runtime' -Encoding ASCII
    Remove-Item -LiteralPath $Downloads -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root '_cache') -Recurse -Force -ErrorAction SilentlyContinue
}

function Read-JsonIfPresent([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    return $null
}

function Resolve-Connections {
    $PaddleConnection = Read-JsonIfPresent (Join-Path $Root '..\paddleocr\connection.json')
    if (-not $PaddleConnection) {
        $PaddleConfig = Read-JsonIfPresent (Join-Path $Root '..\paddleocr\config.json')
        if ($PaddleConfig) {
            $PaddleConnection = [pscustomobject]@{ url="http://$($PaddleConfig.host):$($PaddleConfig.port)"; api_key=$PaddleConfig.api_key }
        }
    }
    $LlamaConnection = Read-JsonIfPresent (Join-Path $Root '..\llama\connection.json')
    if (-not $LlamaConnection) {
        $LlamaConfig = Read-JsonIfPresent (Join-Path $Root '..\llama\config.json')
        if ($LlamaConfig) {
            $LlamaConnection = [pscustomobject]@{ chat_url="http://$($LlamaConfig.host):$($LlamaConfig.llm_port)/v1"; chat_api_key=$LlamaConfig.api_key }
        }
    }
    if (-not $PaddleUrl -and $PaddleConnection) { $script:PaddleUrl = [string]$PaddleConnection.url }
    if (-not $PaddleKey -and $PaddleConnection) { $script:PaddleKey = [string]$PaddleConnection.api_key }
    if (-not $LlamaUrl -and $LlamaConnection) { $script:LlamaUrl = [string]$LlamaConnection.chat_url }
    if (-not $LlamaKey -and $LlamaConnection) { $script:LlamaKey = [string]$LlamaConnection.chat_api_key }
    if (-not $PaddleUrl -or -not $PaddleKey) { throw 'PaddleOCR settings not found. Use -PaddleUrl URL -PaddleKey KEY.' }
    if (-not $LlamaUrl) { throw 'llama.cpp settings not found. Use -LlamaUrl URL [-LlamaKey KEY].' }
}

function Quote-Argument([object]$Value) {
    $Text = [string]$Value
    if ($Text.Length -eq 0) { return '""' }
    if ($Text -notmatch '[\s"]') { return $Text }
    return '"' + $Text.Replace('"','\"') + '"'
}

function Wait-Healthy([string]$Url,[Diagnostics.Process]$Process) {
    $Deadline = [DateTime]::UtcNow.AddMinutes(2)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Process.HasExited) { throw "Digitizer exited with code $($Process.ExitCode). See logs/." }
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($Response.StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    throw "Digitizer did not become ready at $Url. See logs/."
}

Initialize-PortableEnvironment
if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $Runtime '.installed') -PathType Leaf)) {
    Install-Digitizer
    Initialize-PortableEnvironment
}
Resolve-Connections
try {
    Invoke-WebRequest -UseBasicParsing -Uri "$($PaddleUrl.TrimEnd('/'))/health" -TimeoutSec 5 | Out-Null
} catch {
    throw "PaddleOCR is not running at $PaddleUrl. Start paddleocr\start.bat first or pass another URL."
}

$Arguments = @((Join-Path $Root 'app.py'),'--host',$Config.host,'--port',$Port,'--data-root',$Data,'--paddle-url',$PaddleUrl,'--paddle-token',$PaddleKey,'--llama-url',$LlamaUrl,'--llama-key',$LlamaKey)
$ArgumentLine = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
$OutLog = Join-Path $Logs 'app.out.log'
$ErrorLog = Join-Path $Logs 'app.error.log'
Remove-Item -LiteralPath $OutLog,$ErrorLog -Force -ErrorAction SilentlyContinue
$Process = $null
try {
    $Process = Start-Process -FilePath $Python -ArgumentList $ArgumentLine -WorkingDirectory $Root -NoNewWindow -RedirectStandardOutput $OutLog -RedirectStandardError $ErrorLog -PassThru
    $Url = "http://$($Config.host):$Port"
    Wait-Healthy "$Url/health" $Process
    Write-Host "[READY] Digitizer: $Url" -ForegroundColor Green
    try { Start-Process $Url } catch { Write-Warning "Open $Url in a browser." }
    Read-Host 'Press Enter to stop digitizer' | Out-Null
} finally {
    if ($Process -and -not $Process.HasExited) {
        Write-Host 'Stopping digitizer...'
        $Process.Kill()
        $Process.WaitForExit(10000) | Out-Null
    }
    if ($Process) { $Process.Dispose() }
}
