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
if ($VsCodeMaxOutputTokens -le 0 -or $VsCodeMaxOutputTokens -ge 4096) {
    throw 'vscode_max_output_tokens must be greater than 0 and less than 4096.'
}

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

function New-VsCodeProvider([string]$ModelId,[bool]$ToolCalling,[int]$Context) {
    return [pscustomobject][ordered]@{
        name = 'Local llama.cpp'
        vendor = 'customendpoint'
        apiKey = [string]$Config.api_key
        apiType = 'chat-completions'
        models = @(
            [pscustomobject][ordered]@{
                id = $ModelId
                name = "$ModelId (local)"
                url = $ChatCompletionsUrl
                toolCalling = $ToolCalling
                vision = [bool]$Config.vscode_vision
                maxInputTokens = $Context - $VsCodeMaxOutputTokens
                maxOutputTokens = $VsCodeMaxOutputTokens
            }
        )
    }
}

function Write-Connection {
    $ChatStatus = 'stopped'
    $ChatModel = $null
    $EmbeddingStatus = 'stopped'
    $EmbeddingModel = $null
    $ChatToolCalling = $false
    $ChatContext = [int]$Config.llm_context
    if ($Running.ContainsKey('llm') -and -not $Running['llm'].Process.HasExited) {
        $ChatStatus = 'running'
        $ChatModel = $Running['llm'].ModelId
        $ChatToolCalling = [bool]$Running['llm'].ToolCalling
        $ChatContext = [int]$Running['llm'].Context
    }
    if ($Running.ContainsKey('embed') -and -not $Running['embed'].Process.HasExited) {
        $EmbeddingStatus = 'running'
        $EmbeddingModel = $Running['embed'].ModelId
    }
    $Connection = [ordered]@{
        schema = 2
        chat_status = $ChatStatus
        chat_url = $ChatBaseUrl
        chat_completions_url = $ChatCompletionsUrl
        chat_models_url = $ChatModelsUrl
        chat_api_key = [string]$Config.api_key
        chat_model = $ChatModel
        chat_context_tokens = $ChatContext
        chat_max_input_tokens = $ChatContext - $VsCodeMaxOutputTokens
        chat_max_output_tokens = $VsCodeMaxOutputTokens
        chat_tool_calling = $ChatToolCalling
        chat_vision = [bool]$Config.vscode_vision
        embedding_status = $EmbeddingStatus
        embedding_url = $EmbeddingBaseUrl
        embedding_endpoint = $EmbeddingUrl
        embedding_api_key = [string]$Config.api_key
        embedding_model = $EmbeddingModel
    }
    $Connection | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'connection.json') -Encoding UTF8
    if ($ChatModel) {
        $VsCodeProviders = @(New-VsCodeProvider -ModelId $ChatModel -ToolCalling $ChatToolCalling -Context $ChatContext)
        ConvertTo-Json -InputObject $VsCodeProviders -Depth 8 |
            Set-Content -LiteralPath (Join-Path $Root 'vscode-model.json') -Encoding UTF8
    }
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

function Get-PropertyValue([object]$Object,[string]$Name) {
    if ($null -eq $Object) { return $null }
    $Property = $Object.PSObject.Properties[$Name]
    if ($Property) { return $Property.Value }
    return $null
}

function Get-ServerProperties([Diagnostics.Process]$Process) {
    if ($Process.HasExited) { throw 'llama-server exited before its tool template could be checked.' }
    $Headers = @{ Authorization = "Bearer $($Config.api_key)" }
    try {
        return Invoke-RestMethod -UseBasicParsing -Uri "http://${ClientHost}:$($Config.llm_port)/props" -Headers $Headers -TimeoutSec 10
    } catch {
        throw "Cannot inspect llama.cpp /props for tool support: $($_.Exception.Message)"
    }
}

function Confirm-ToolSupport([object]$Props) {
    if (-not [bool]$Config.vscode_tool_calling) { return $false }
    $Caps = Get-PropertyValue $Props 'chat_template_caps'
    if ($Caps -and [bool](Get-PropertyValue $Caps 'supports_tools')) { return $true }
    $ToolTemplate = [string](Get-PropertyValue $Props 'chat_template_tool_use')
    if ($ToolTemplate) { return $true }
    $ChatTemplate = [string](Get-PropertyValue $Props 'chat_template')
    if ($ChatTemplate -match '(?i)\btools?\b') { return $true }
    throw @'
The selected GGUF does not expose a tool-aware chat template.
Use a tool-capable instruct model (recommended: Qwen2.5-7B-Instruct Q4_K_M).
The server was started with --jinja, but /props reports no tool support.
'@
}

function Get-ActualContext([object]$Props,[int]$RequestedContext) {
    $Generation = Get-PropertyValue $Props 'default_generation_settings'
    $ActualContext = [int](Get-PropertyValue $Generation 'n_ctx')
    if ($ActualContext -le 0) { return $RequestedContext }
    if ($ActualContext -le $VsCodeMaxOutputTokens) {
        throw "llama.cpp reported an unusable context size: $ActualContext"
    }
    if ($ActualContext -ne $RequestedContext) {
        Write-Host "llama.cpp adjusted context from $RequestedContext to $ActualContext tokens." -ForegroundColor Yellow
    }
    return $ActualContext
}

function Select-LlmContext {
    Write-Host ''
    Write-Host 'Select LLM context:' -ForegroundColor Cyan
    Write-Host '  [1]  16K   fastest and safest'
    Write-Host '  [2]  32K   native Qwen2.5 context'
    Write-Host '  [3]  64K   long context, quantized KV cache'
    Write-Host '  [4] 128K   maximum Qwen2.5 context; 2 x 8 GB GPUs exclusively'
    Write-Host '  [C] custom token count (4096..131072)'
    $Choice = (Read-Host 'Context (Enter = 128K)').Trim()
    switch ($Choice.ToLowerInvariant()) {
        ''  { return 131072 }
        '1' { return 16384 }
        '2' { return 32768 }
        '3' { return 65536 }
        '4' { return 131072 }
        'c' {
            $Raw = (Read-Host 'Exact context tokens (4096..131072)').Trim()
            $Context = 0
            if (-not [int]::TryParse($Raw,[ref]$Context) -or $Context -lt 4096 -or $Context -gt 131072) {
                Write-Host 'Context must be an integer from 4096 through 131072.' -ForegroundColor Yellow
                return $null
            }
            if ($Context -le $VsCodeMaxOutputTokens) {
                Write-Host 'Context must be greater than max output tokens.' -ForegroundColor Yellow
                return $null
            }
            return $Context
        }
        default {
            Write-Host 'Invalid context choice.' -ForegroundColor Yellow
            return $null
        }
    }
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

function Get-ModelCatalog([string]$Role,[bool]$WarnIncomplete = $false) {
    $Directory = Join-Path $Models $Role
    $Files = @(Get-ChildItem -LiteralPath $Directory -Filter '*.gguf' -File | Sort-Object Name)
    $Entries = [Collections.Generic.List[object]]::new()
    $SeenSplitSets = @{}

    foreach ($File in $Files) {
        if ($File.Name -notmatch '^(?<Stem>.+)-(?<Part>\d{5})-of-(?<Total>\d{5})\.gguf$') {
            $Entries.Add([pscustomobject]@{
                File = $File
                ModelId = $File.BaseName
                DisplayName = $File.Name
                ShardCount = 1
            })
            continue
        }

        $Stem = [string]$Matches['Stem']
        $TotalText = [string]$Matches['Total']
        $Total = [int]$TotalText
        $SetKey = "$($Stem.ToLowerInvariant())|$TotalText"
        if ($SeenSplitSets.ContainsKey($SetKey)) { continue }
        $SeenSplitSets[$SetKey] = $true

        $Missing = [Collections.Generic.List[string]]::new()
        if ($Total -lt 1) {
            $Missing.Add('invalid shard count')
        } else {
            for ($Part = 1; $Part -le $Total; $Part++) {
                $ShardName = '{0}-{1:D5}-of-{2}.gguf' -f $Stem,$Part,$TotalText
                if (-not (Test-Path -LiteralPath (Join-Path $Directory $ShardName) -PathType Leaf)) {
                    $Missing.Add($ShardName)
                }
            }
        }
        if ($Missing.Count) {
            if ($WarnIncomplete) {
                Write-Host "Ignoring incomplete split model '$Stem': missing $($Missing -join ', ')" -ForegroundColor Yellow
            }
            continue
        }

        $FirstShardName = '{0}-{1:D5}-of-{2}.gguf' -f $Stem,1,$TotalText
        $Entries.Add([pscustomobject]@{
            File = (Get-Item -LiteralPath (Join-Path $Directory $FirstShardName))
            ModelId = $Stem
            DisplayName = "$Stem ($Total GGUF parts)"
            ShardCount = $Total
        })
    }

    return @($Entries | Sort-Object DisplayName)
}

function Select-Model([string]$Role) {
    $Items = @(Get-ModelCatalog $Role $true)
    if (-not $Items.Count) {
        Write-Host "No complete GGUF models in models\$Role" -ForegroundColor Yellow
        return $null
    }
    for ($Index = 0; $Index -lt $Items.Count; $Index++) {
        Write-Host ('  [{0}] {1}' -f ($Index + 1),$Items[$Index].DisplayName)
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
    $ModelEntry = Select-Model $Role
    if (-not $ModelEntry) { return }
    $Model = $ModelEntry.File
    $RequestedContext = $null
    $CacheType = $null
    $TensorSplit = $null
    if ($Role -eq 'llm') {
        $RequestedContext = Select-LlmContext
        if ($null -eq $RequestedContext) { return }
    }
    Stop-Role $Role
    $ModelId = $ModelEntry.ModelId
    if ($Role -eq 'llm') {
        $Port = [int]$Config.llm_port
        $TensorSplit = [string]$Config.llm_tensor_split
        $CacheType = 'f16'
        if ($RequestedContext -gt 32768) {
            $TensorSplit = [string]$Config.llm_long_context_tensor_split
            $CacheType = 'q8_0'
        }
        $Arguments = @('--model',$Model.FullName,'--alias',$ModelId,'--host',$Config.host,'--port',$Port,'--api-key',$Config.api_key,'--n-gpu-layers','999','--split-mode','layer','--main-gpu',$Config.llm_main_gpu,'--tensor-split',$TensorSplit,'--ctx-size',$RequestedContext,'--batch-size',$Config.llm_batch,'--parallel','1','--fit','off','--cache-type-k',$CacheType,'--cache-type-v',$CacheType,'--flash-attn','on','--no-webui')
        if ($RequestedContext -gt 32768) {
            if ($ModelId -match '(?i)qwen2[._-]?5.*7b.*instruct') {
                $RopeScale = ($RequestedContext / 32768.0).ToString('0.########',[Globalization.CultureInfo]::InvariantCulture)
                $Arguments += @('--rope-scaling','yarn','--rope-scale',$RopeScale,'--yarn-orig-ctx','32768')
            } else {
                Write-Host 'Context above 32K uses quantized KV cache, but automatic YaRN is only enabled for Qwen2.5 7B Instruct.' -ForegroundColor Yellow
                Write-Host 'The selected model must provide its own long-context metadata.' -ForegroundColor Yellow
            }
        }
        if ([bool]$Config.vscode_tool_calling) { $Arguments += '--jinja' }
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
        $ToolCalling = $false
        $ActualContext = $null
        if ($Role -eq 'llm') {
            $Props = Get-ServerProperties $Process
            $ToolCalling = Confirm-ToolSupport $Props
            $ActualContext = Get-ActualContext $Props $RequestedContext
        }
        $Running[$Role] = [pscustomobject]@{ Process=$Process; Model=$ModelEntry.DisplayName; ModelId=$ModelId; Port=$Port; ToolCalling=$ToolCalling; Context=$ActualContext; CacheType=$CacheType; TensorSplit=$TensorSplit }
        Write-Connection
        $ReadyUrl = if ($Role -eq 'llm') { $ChatCompletionsUrl } else { $EmbeddingUrl }
        Write-Host "[READY] $Role on $ReadyUrl" -ForegroundColor Green
        if ($Role -eq 'llm') {
            Write-Host "  Context $ActualContext tokens; KV cache $CacheType; tensor split $TensorSplit"
            Write-Host "  Copy llama\vscode-model.json into portable VS Code chatLanguageModels.json."
        }
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
            $Count = @(Get-ModelCatalog $Role).Count
            Write-Host ("{0,-6} stopped  models={1}" -f $Role,$Count)
        }
    }
    $ModelId = '(start LLM and select a model)'
    if ($Running.ContainsKey('llm') -and -not $Running['llm'].Process.HasExited) {
        $ModelId = $Running['llm'].ModelId
    }
    $ToolCalling = [bool]$Config.vscode_tool_calling
    $DisplayContext = [int]$Config.llm_context
    if ($Running.ContainsKey('llm') -and -not $Running['llm'].Process.HasExited) {
        $ToolCalling = [bool]$Running['llm'].ToolCalling
        $DisplayContext = [int]$Running['llm'].Context
    }
    Write-Host ''
    Write-Host 'VS Code connection (Custom Endpoint)' -ForegroundColor Cyan
    Write-Host ("  API type    Chat Completions")
    Write-Host ("  Endpoint    {0}" -f $ChatCompletionsUrl)
    Write-Host ("  API key     {0}" -f $Config.api_key)
    Write-Host ("  Model ID    {0}" -f $ModelId)
    Write-Host ("  Context     {0} input + {1} output = {2} tokens" -f ($DisplayContext - $VsCodeMaxOutputTokens),$VsCodeMaxOutputTokens,$DisplayContext)
    Write-Host ("  Tools       {0}" -f $ToolCalling)
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
