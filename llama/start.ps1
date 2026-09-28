[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Runtime = Join-Path $Root 'llama-cpp'
$Server = Join-Path $Runtime 'llama-server.exe'
$Source = Join-Path $Root '_src\llama-b10786-bin-win-vulkan-x64.zip'
$SourceUrl = 'https://github.com/ggml-org/llama.cpp/releases/download/b10786/llama-b10786-bin-win-vulkan-x64.zip'
$Models = Join-Path $Root 'models'
$Logs = Join-Path $Root 'logs'
$Config = Get-Content -LiteralPath (Join-Path $Root 'config.json') -Raw | ConvertFrom-Json
$ClientHost = [string]$Config.host
if ($ClientHost -eq '0.0.0.0') { $ClientHost = '127.0.0.1' }
if ($ClientHost -eq '::') { $ClientHost = '[::1]' }
$ChatBaseUrl = "http://${ClientHost}:$($Config.llm_port)/v1"
$ChatCompletionsUrl = "$ChatBaseUrl/chat/completions"
$ChatModelsUrl = "$ChatBaseUrl/models"
$EmbeddingBaseUrl = "http://${ClientHost}:$($Config.embed_port)/v1"
$EmbeddingUrl = "$EmbeddingBaseUrl/embeddings"
$VsCodeMaxOutputTokens = [int]$Config.vscode_max_output_tokens
if ($VsCodeMaxOutputTokens -le 0 -or $VsCodeMaxOutputTokens -ge [int]$Config.llm_context) {
    throw 'vscode_max_output_tokens must be greater than 0 and less than llm_context.'
}
$VsCodeMaxInputTokens = [int]$Config.llm_context - $VsCodeMaxOutputTokens

function Initialize-PortableEnvironment {
    $PortableProfile = Join-Path $Root '_profile'
    $PortableTemp = Join-Path $Root '_temp'
    New-Item -ItemType Directory -Path $PortableProfile,$PortableTemp,$Logs,(Join-Path $Models 'llm'),(Join-Path $Models 'embed'),(Split-Path $Source -Parent) -Force | Out-Null
    $env:USERPROFILE = $PortableProfile
    $env:APPDATA = Join-Path $PortableProfile 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $PortableProfile 'AppData\Local'
    $env:TEMP = $PortableTemp
    $env:TMP = $PortableTemp
}

function Download-File([string]$Url,[string]$Destination) {
    New-Item -ItemType Directory -Path (Split-Path $Destination -Parent) -Force | Out-Null
    $Partial = "$Destination.partial"
    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    Write-Host "Downloading $Url"
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
}

function Get-7Zip {
    $Tools = Join-Path $Root '_tools'
    $SevenZip = Join-Path $Tools 'x64\7za.exe'
    if (Test-Path -LiteralPath $SevenZip -PathType Leaf) { return $SevenZip }
    New-Item -ItemType Directory -Path $Tools -Force | Out-Null
    $Bootstrap = Join-Path $Tools '7zr.exe'
    $Extra = Join-Path $Tools '7z2602-extra.7z'
    if (-not (Test-Path -LiteralPath $Bootstrap -PathType Leaf)) {
        Download-File 'https://github.com/ip7z/7zip/releases/download/26.02/7zr.exe' $Bootstrap
    }
    if (-not (Test-Path -LiteralPath $Extra -PathType Leaf)) {
        Download-File 'https://github.com/ip7z/7zip/releases/download/26.02/7z2602-extra.7z' $Extra
    }
    & $Bootstrap x -y "-o$Tools" $Extra | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $SevenZip -PathType Leaf)) {
        throw 'Cannot prepare portable 7-Zip.'
    }
    return $SevenZip
}

function Install-Llama {
    while (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        Write-Host ''
        Write-Host 'llama.cpp archive is missing.' -ForegroundColor Yellow
        Write-Host "Download: $SourceUrl"
        Write-Host "Put it at: $Source"
        $Answer = Read-Host '[D] download automatically, [Enter] check again, [Q] quit'
        if ($Answer -match '^[Qq]$') { throw 'Installation cancelled.' }
        if ($Answer -match '^[Dd]$') { Download-File $SourceUrl $Source }
    }
    $SevenZip = Get-7Zip
    New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
    Write-Host 'Extracting llama.cpp...'
    & $SevenZip x -y "-o$Runtime" $Source | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Server -PathType Leaf)) {
        throw "Archive did not produce $Server"
    }
}

function Write-Connection {
    $ChatStatus = 'stopped'
    $ChatModel = $null
    $EmbeddingStatus = 'stopped'
    $EmbeddingModel = $null
    if ($Running.ContainsKey('llm') -and -not $Running['llm'].Process.HasExited) {
        $ChatStatus = 'running'
        $ChatModel = $Running['llm'].ModelId
    }
    if ($Running.ContainsKey('embed') -and -not $Running['embed'].Process.HasExited) {
        $EmbeddingStatus = 'running'
        $EmbeddingModel = $Running['embed'].ModelId
    }
    $Connection = [ordered]@{
        schema = 1
        chat_status = $ChatStatus
        chat_url = $ChatBaseUrl
        chat_completions_url = $ChatCompletionsUrl
        chat_models_url = $ChatModelsUrl
        chat_api_key = [string]$Config.api_key
        chat_model = $ChatModel
        chat_context_tokens = [int]$Config.llm_context
        chat_max_input_tokens = $VsCodeMaxInputTokens
        chat_max_output_tokens = $VsCodeMaxOutputTokens
        chat_tool_calling = [bool]$Config.vscode_tool_calling
        chat_vision = [bool]$Config.vscode_vision
        embedding_status = $EmbeddingStatus
        embedding_url = $EmbeddingBaseUrl
        embedding_endpoint = $EmbeddingUrl
        embedding_api_key = [string]$Config.api_key
        embedding_model = $EmbeddingModel
    }
    $Connection | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'connection.json') -Encoding UTF8
}

function Quote-Argument([object]$Value) {
    $Text = [string]$Value
    if ($Text.Length -eq 0) { return '""' }
    if ($Text -notmatch '[\s"]') { return $Text }
    return '"' + $Text.Replace('"','\"') + '"'
}

function Wait-Healthy([string]$Url,[Diagnostics.Process]$Process) {
    $Deadline = [DateTime]::UtcNow.AddMinutes(5)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Process.HasExited) { throw "llama-server exited with code $($Process.ExitCode). See logs/." }
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($Response.StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "llama-server did not become ready at $Url. See logs/."
}

Initialize-PortableEnvironment
if (-not (Test-Path -LiteralPath $Server -PathType Leaf)) { Install-Llama }
& $Server --version
if ($LASTEXITCODE -ne 0) { throw 'llama-server.exe cannot start.' }

$Running = @{}
Write-Connection

function Stop-Role([string]$Role) {
    if ($Running.ContainsKey($Role)) {
        $Entry = $Running[$Role]
        if (-not $Entry.Process.HasExited) {
            Write-Host "Stopping $Role..."
            $Entry.Process.Kill()
            $Entry.Process.WaitForExit(10000) | Out-Null
        }
        $Entry.Process.Dispose()
        $Running.Remove($Role) | Out-Null
        Write-Connection
    }
}

function Select-Model([string]$Role) {
    $Items = @(Get-ChildItem -LiteralPath (Join-Path $Models $Role) -Filter '*.gguf' -File | Sort-Object Name)
    if (-not $Items.Count) {
        Write-Host "No GGUF files in models\$Role" -ForegroundColor Yellow
        return $null
    }
    for ($Index = 0; $Index -lt $Items.Count; $Index++) {
        Write-Host ('  [{0}] {1}' -f ($Index + 1),$Items[$Index].Name)
    }
    $Choice = Read-Host 'Model number (Enter cancels)'
    if (-not $Choice) { return $null }
    $Number = 0
    if (-not [int]::TryParse($Choice,[ref]$Number) -or $Number -lt 1 -or $Number -gt $Items.Count) {
        Write-Host 'Invalid model number.' -ForegroundColor Yellow
        return $null
    }
    return $Items[$Number - 1]
}

function Start-Role([string]$Role) {
    $Model = Select-Model $Role
    if (-not $Model) { return }
    Stop-Role $Role
    $ModelId = $Model.BaseName
    if ($Role -eq 'llm') {
        $Port = [int]$Config.llm_port
        $Arguments = @('--model',$Model.FullName,'--alias',$ModelId,'--host',$Config.host,'--port',$Port,'--api-key',$Config.api_key,'--n-gpu-layers','999','--split-mode','layer','--main-gpu',$Config.llm_main_gpu,'--tensor-split',$Config.llm_tensor_split,'--ctx-size',$Config.llm_context,'--batch-size',$Config.llm_batch,'--parallel','1','--no-webui')
    } else {
        $Port = [int]$Config.embed_port
        $Arguments = @('--model',$Model.FullName,'--alias',$ModelId,'--host',$Config.host,'--port',$Port,'--api-key',$Config.api_key,'--embedding','--pooling','last','--n-gpu-layers','999','--split-mode','none','--main-gpu',$Config.embed_gpu,'--ctx-size',$Config.embed_context,'--batch-size',$Config.embed_batch,'--no-webui')
    }
    $OutLog = Join-Path $Logs "$Role.out.log"
    $ErrorLog = Join-Path $Logs "$Role.error.log"
    Remove-Item -LiteralPath $OutLog,$ErrorLog -Force -ErrorAction SilentlyContinue
    $ArgumentLine = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
    $Process = Start-Process -FilePath $Server -ArgumentList $ArgumentLine -WorkingDirectory $Runtime -NoNewWindow -RedirectStandardOutput $OutLog -RedirectStandardError $ErrorLog -PassThru
    try {
        Wait-Healthy "http://${ClientHost}:$Port/health" $Process
        $Running[$Role] = [pscustomobject]@{ Process=$Process; Model=$Model.Name; ModelId=$ModelId; Port=$Port }
        Write-Connection
        $ReadyUrl = if ($Role -eq 'llm') { $ChatCompletionsUrl } else { $EmbeddingUrl }
        Write-Host "[READY] $Role on $ReadyUrl" -ForegroundColor Green
    } catch {
        if (-not $Process.HasExited) { $Process.Kill() }
        $Process.Dispose()
        throw
    }
}

function Show-Status {
    Clear-Host
    Write-Host 'Portable llama.cpp (OpenAI-compatible API)' -ForegroundColor Cyan
    foreach ($Role in @('llm','embed')) {
        if ($Running.ContainsKey($Role) -and -not $Running[$Role].Process.HasExited) {
            $Entry = $Running[$Role]
            Write-Host ("{0,-6} RUNNING  pid={1} port={2} model={3}" -f $Role,$Entry.Process.Id,$Entry.Port,$Entry.Model) -ForegroundColor Green
        } else {
            if ($Running.ContainsKey($Role)) { Stop-Role $Role }
            $Count = @(Get-ChildItem -LiteralPath (Join-Path $Models $Role) -Filter '*.gguf' -File).Count
            Write-Host ("{0,-6} stopped  models={1}" -f $Role,$Count)
        }
    }
    $ModelId = '(start LLM and select a model)'
    if ($Running.ContainsKey('llm') -and -not $Running['llm'].Process.HasExited) {
        $ModelId = $Running['llm'].ModelId
    }
    Write-Host ''
    Write-Host 'VS Code connection (Custom Endpoint)' -ForegroundColor Cyan
    Write-Host ("  API type    Chat Completions")
    Write-Host ("  Endpoint    {0}" -f $ChatCompletionsUrl)
    Write-Host ("  API key     {0}" -f $Config.api_key)
    Write-Host ("  Model ID    {0}" -f $ModelId)
    Write-Host ("  Context     {0} input + {1} output = {2} tokens" -f $VsCodeMaxInputTokens,$VsCodeMaxOutputTokens,$Config.llm_context)
    Write-Host ("  Tools       {0}" -f [bool]$Config.vscode_tool_calling)
    Write-Host ("  Vision      {0}" -f [bool]$Config.vscode_vision)
    Write-Host ("  Models API  {0}" -f $ChatModelsUrl)
    Write-Host ("  Embeddings  {0}" -f $EmbeddingUrl)
    Write-Host ''
    Write-Host '[1] Start LLM      [2] Stop LLM'
    Write-Host '[3] Start embed    [4] Stop embed'
    Write-Host '[R] Refresh        [Q] Stop all and exit'
}

try {
    while ($true) {
        Show-Status
        $Choice = Read-Host 'Select'
        switch ($Choice.ToLowerInvariant()) {
            '1' { Start-Role 'llm' }
            '2' { Stop-Role 'llm' }
            '3' { Start-Role 'embed' }
            '4' { Stop-Role 'embed' }
            'q' { break }
            default {}
        }
        if ($Choice.ToLowerInvariant() -eq 'q') { break }
    }
} finally {
    Stop-Role 'embed'
    Stop-Role 'llm'
}
