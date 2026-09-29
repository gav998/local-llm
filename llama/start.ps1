[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$UserAppData = $env:APPDATA
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

function New-VsCodeProvider([string]$ModelId,[bool]$ToolCalling) {
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
                maxInputTokens = $VsCodeMaxInputTokens
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
    if ($Running.ContainsKey('llm') -and -not $Running['llm'].Process.HasExited) {
        $ChatStatus = 'running'
        $ChatModel = $Running['llm'].ModelId
        $ChatToolCalling = [bool]$Running['llm'].ToolCalling
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
        chat_context_tokens = [int]$Config.llm_context
        chat_max_input_tokens = $VsCodeMaxInputTokens
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
        @(New-VsCodeProvider -ModelId $ChatModel -ToolCalling $ChatToolCalling) |
            ConvertTo-Json -Depth 8 |
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

function Confirm-ToolSupport([Diagnostics.Process]$Process) {
    if (-not [bool]$Config.vscode_tool_calling) { return $false }
    if ($Process.HasExited) { throw 'llama-server exited before its tool template could be checked.' }
    $Headers = @{ Authorization = "Bearer $($Config.api_key)" }
    try {
        $Props = Invoke-RestMethod -UseBasicParsing -Uri "http://${ClientHost}:$($Config.llm_port)/props" -Headers $Headers -TimeoutSec 10
    } catch {
        throw "Cannot inspect llama.cpp /props for tool support: $($_.Exception.Message)"
    }
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

function Get-VsCodeConfigurationTargets {
    $Targets = @()
    if (-not $UserAppData) { return $Targets }
    foreach ($Product in @('Code','Code - Insiders')) {
        $UserDirectory = Join-Path (Join-Path $UserAppData $Product) 'User'
        if (-not (Test-Path -LiteralPath $UserDirectory -PathType Container)) { continue }
        $Targets += [pscustomobject]@{
            Label = "$Product - Default profile"
            Path = Join-Path $UserDirectory 'chatLanguageModels.json'
        }
        $Profiles = Join-Path $UserDirectory 'profiles'
        if (Test-Path -LiteralPath $Profiles -PathType Container) {
            Get-ChildItem -LiteralPath $Profiles -Directory | ForEach-Object {
                $ProfileConfig = Join-Path $_.FullName 'chatLanguageModels.json'
                if (Test-Path -LiteralPath $ProfileConfig -PathType Leaf) {
                    $Targets += [pscustomobject]@{
                        Label = "$Product - profile $($_.Name)"
                        Path = $ProfileConfig
                    }
                }
            }
        }
    }
    return @($Targets)
}

function Select-VsCodeConfigurationTarget {
    $Targets = @(Get-VsCodeConfigurationTargets)
    if (-not $Targets.Count) {
        throw 'VS Code user profile was not found. Start VS Code once, then retry.'
    }
    if ($Targets.Count -eq 1) { return $Targets[0] }
    Write-Host ''
    Write-Host 'Select the VS Code profile to configure:' -ForegroundColor Cyan
    for ($Index = 0; $Index -lt $Targets.Count; $Index++) {
        Write-Host ('  [{0}] {1}' -f ($Index + 1),$Targets[$Index].Label)
    }
    $Choice = Read-Host 'Profile number (Enter cancels)'
    if (-not $Choice) { return $null }
    $Number = 0
    if (-not [int]::TryParse($Choice,[ref]$Number) -or $Number -lt 1 -or $Number -gt $Targets.Count) {
        Write-Host 'Invalid profile number.' -ForegroundColor Yellow
        return $null
    }
    return $Targets[$Number - 1]
}

function Install-VsCodeConfiguration {
    if (-not $Running.ContainsKey('llm') -or $Running['llm'].Process.HasExited) {
        Write-Host 'Start the LLM first, then configure VS Code.' -ForegroundColor Yellow
        return
    }
    $Target = Select-VsCodeConfigurationTarget
    if (-not $Target) { return }
    $Providers = @()
    if (Test-Path -LiteralPath $Target.Path -PathType Leaf) {
        $Raw = Get-Content -LiteralPath $Target.Path -Raw
        if ($Raw.Trim()) {
            try {
                $Providers = @($Raw | ConvertFrom-Json)
            } catch {
                throw "Cannot read $($Target.Path) as JSON. Fix it in VS Code and retry."
            }
        }
    }
    $Providers = @($Providers | Where-Object {
        -not ($_.vendor -eq 'customendpoint' -and $_.name -eq 'Local llama.cpp')
    })
    $Entry = $Running['llm']
    $Providers += New-VsCodeProvider -ModelId $Entry.ModelId -ToolCalling ([bool]$Entry.ToolCalling)
    $Directory = Split-Path $Target.Path -Parent
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $Backup = $null
    if (Test-Path -LiteralPath $Target.Path -PathType Leaf) {
        $Backup = "$($Target.Path).backup-$([DateTime]::Now.ToString('yyyyMMdd-HHmmssfff'))"
        Copy-Item -LiteralPath $Target.Path -Destination $Backup
    }
    $Json = $Providers | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Target.Path,$Json,(New-Object Text.UTF8Encoding($false)))
    Write-Host ''
    Write-Host '[READY] VS Code model configuration updated.' -ForegroundColor Green
    Write-Host "  File   $($Target.Path)"
    if ($Backup) { Write-Host "  Backup $Backup" }
    Write-Host 'Reload VS Code, select Session Target: Local, Agent mode, then this model.'
    Read-Host 'Press Enter to continue' | Out-Null
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
        $ToolCalling = if ($Role -eq 'llm') { Confirm-ToolSupport $Process } else { $false }
        $Running[$Role] = [pscustomobject]@{ Process=$Process; Model=$Model.Name; ModelId=$ModelId; Port=$Port; ToolCalling=$ToolCalling }
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
    $ToolCalling = [bool]$Config.vscode_tool_calling
    if ($Running.ContainsKey('llm') -and -not $Running['llm'].Process.HasExited) {
        $ToolCalling = [bool]$Running['llm'].ToolCalling
    }
    Write-Host ''
    Write-Host 'VS Code connection (Custom Endpoint)' -ForegroundColor Cyan
    Write-Host ("  API type    Chat Completions")
    Write-Host ("  Endpoint    {0}" -f $ChatCompletionsUrl)
    Write-Host ("  API key     {0}" -f $Config.api_key)
    Write-Host ("  Model ID    {0}" -f $ModelId)
    Write-Host ("  Context     {0} input + {1} output = {2} tokens" -f $VsCodeMaxInputTokens,$VsCodeMaxOutputTokens,$Config.llm_context)
    Write-Host ("  Tools       {0}" -f $ToolCalling)
    Write-Host ("  Vision      {0}" -f [bool]$Config.vscode_vision)
    Write-Host ("  Models API  {0}" -f $ChatModelsUrl)
    Write-Host ("  Embeddings  {0}" -f $EmbeddingUrl)
    Write-Host ''
    Write-Host '[1] Start LLM      [2] Stop LLM'
    Write-Host '[3] Start embed    [4] Stop embed'
    Write-Host '[5] Configure VS Code automatically'
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
            '5' { Install-VsCodeConfiguration }
            'q' { break }
            default {}
        }
        if ($Choice.ToLowerInvariant() -eq 'q') { break }
    }
} finally {
    Stop-Role 'embed'
    Stop-Role 'llm'
}
