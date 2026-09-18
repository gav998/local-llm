[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$Python=Join-Path $PSScriptRoot 'runtime\python\python.exe';$Rag=Join-Path $PSScriptRoot 'ragflow';$Port=9380;$ModulesRoot=Split-Path -Parent $PSScriptRoot;$Secrets=Join-Path $StateRoot 'secrets.json';$JavaHome=Join-Path $PSScriptRoot 'runtime\java';$JavaExe=Join-Path $JavaHome 'bin\java.exe';$TikaJar=Join-Path $Rag 'tika-server-standard-3.3.0.jar'
function Connection([string]$Name){Read-Json (Join-Path $ModulesRoot "$Name\state\connection.json")}
function Ensure-EmbeddingContextPatch{
 $Path=Join-Path $Rag 'api\db\services\tenant_llm_service.py';if(-not(Test-Path $Path)){return}
 $Text=Get-Content -LiteralPath $Path -Raw -Encoding UTF8;if($Text.Contains('LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS')){return}
 $Needle="        self.max_length = model_config.get(`"max_tokens`") or 8192`n`n";if(-not $Text.Contains($Needle)){$Needle="        self.max_length = model_config.get(`"max_tokens`") or 8192`r`n`r`n"}
 if(-not $Text.Contains($Needle)){throw 'Cannot patch RAGFlow embedding max_length guard: audited line not found'}
 $Replacement="        self.max_length = model_config.get(`"max_tokens`") or 8192`n        if model_config.get(`"model_type`") == LLMType.EMBEDDING.value:`n            local_limit = os.environ.get(`"LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS`", `"`").strip()`n            if local_limit:`n                try:`n                    self.max_length = min(self.max_length, max(32, int(local_limit)))`n                except ValueError:`n                    logging.warning(`"Ignoring invalid LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS=%r`", local_limit)`n`n"
 if($Needle.Contains("`r`n")){$Replacement=$Replacement.Replace("`n","`r`n")}
 [IO.File]::WriteAllText($Path,$Text.Replace($Needle,$Replacement),[Text.UTF8Encoding]::new($false))
}
function Set-RagEnvironment{
 Ensure-EmbeddingContextPatch;$ocr=Connection 'paddleocr';$llamaSettings=Read-Json (Join-Path $ModulesRoot 'llama-cpp\config\runtime\settings.json');$tikaTemp=Join-Path $TempRoot 'tika';New-Item -ItemType Directory -Path $tikaTemp,$LogRoot -Force|Out-Null;$env:PYTHONPATH=$Rag;$env:JAVA_HOME=$JavaHome;$env:TIKA_JAVA=$JavaExe;$env:TIKA_PATH=$tikaTemp;$env:TIKA_LOG_PATH=$LogRoot;$env:PATH=(Join-Path $PSScriptRoot 'runtime\vc')+';'+(Join-Path $JavaHome 'bin')+';'+(Split-Path $Python)+';'+$env:SystemRoot+'\System32';$env:NLTK_DATA=Join-Path $PSScriptRoot 'assets\nltk';$env:TIKTOKEN_CACHE_DIR=$Rag;$env:TIKA_SERVER_JAR='file:///'+($TikaJar.Replace('\','/'));$env:PADDLEOCR_BASE_URL=$ocr.url;$env:PADDLEOCR_API_URL=$ocr.url;$env:PADDLEOCR_ACCESS_TOKEN=$ocr.token;$env:LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS=[string]$llamaSettings.embedding_context;$env:HF_HUB_OFFLINE='1';$env:TRANSFORMERS_OFFLINE='1'
}
function Assert-TikaRuntime{
 if(-not(Test-Path -LiteralPath $JavaExe -PathType Leaf)){throw "Portable Java runtime is missing: $JavaExe. Rebuild/re-extract the ragflow module and run LOCAL-LLM.bat install."}
 if(-not(Test-Path -LiteralPath $TikaJar -PathType Leaf)){throw "Tika server JAR is missing: $TikaJar. Rebuild/re-extract the ragflow module and run LOCAL-LLM.bat install."}
 $PreviousErrorActionPreference=$ErrorActionPreference
 $JavaExitCode=-1
 try{
  # java -version writes its normal version banner to stderr. Windows PowerShell
  # otherwise promotes that banner to NativeCommandError when error action is Stop.
  $ErrorActionPreference='Continue'
  & $JavaExe '-version' 2>$null|Out-Null
  $JavaExitCode=$LASTEXITCODE
 }finally{$ErrorActionPreference=$PreviousErrorActionPreference}
 if($JavaExitCode -ne 0){throw "Portable Java runtime cannot start: $JavaExe"}
}
function Assert-CoreDependencies{$mysql=Connection 'mysql';$es=Connection 'elasticsearch';$silo=Connection 'silo';$valkey=Connection 'valkey';if(-not(Test-Tcp ([int]$mysql.port))){throw 'MySQL endpoint is not ready'};if(-not(Test-Http $es.url)){throw 'Elasticsearch endpoint is not ready'};if(-not(Test-Http ("http://$($silo.endpoint)/minio/health/ready"))){throw 'Silo endpoint is not ready'};if(-not(Test-Tcp ([int]$valkey.port))){throw 'Valkey endpoint is not ready'}}
function Assert-IngestionDependencies{$llama=Connection 'llama-cpp';$ocr=Connection 'paddleocr';if(-not(Test-Http ($llama.embedding_url.Replace('/v1','/health')))){throw 'Embedding endpoint is not ready'};if(-not(Test-Http ($ocr.url+'/health'))){throw 'PaddleOCR endpoint is not ready'}}
function Install-Ragflow{
 Begin-Install;$mysql=Connection 'mysql';$es=Connection 'elasticsearch';$silo=Connection 'silo';$valkey=Connection 'valkey';$llama=Connection 'llama-cpp';$ocr=Connection 'paddleocr';if(Test-Path $Secrets){$secret=Read-Json $Secrets}else{$secret=[ordered]@{secret_key=New-HexSecret 32};Write-JsonAtomic $Secrets $secret}
 @"
ragflow:
  host: '127.0.0.1'
  http_port: $Port
  secret_key: '$($secret.secret_key)'
mysql:
  name: '$($mysql.database)'
  user: '$($mysql.username)'
  password: '$($mysql.password)'
  host: '$($mysql.host)'
  port: $($mysql.port)
  max_connections: 100
  stale_timeout: 300
  max_allowed_packet: 1073741824
minio:
  user: '$($silo.username)'
  password: '$($silo.password)'
  host: '$($silo.endpoint)'
  bucket: ''
  prefix_path: ''
es:
  hosts: '$($es.url)'
  verify_certs: false
redis:
  db: $($valkey.database)
  username: ''
  password: '$($valkey.password)'
  host: '$($valkey.host):$($valkey.port)'
user_default_llm:
  factory: 'OpenAI-API-Compatible'
  api_key: '$($llama.api_key)'
  base_url: '$($llama.chat_url)'
  default_models:
    chat_model:
      name: 'Vikhr-Nemo-12B-Q4_K_M'
      factory: 'OpenAI-API-Compatible'
      api_key: '$($llama.api_key)'
      base_url: '$($llama.chat_url)'
    embedding_model:
      name: 'Qwen3-Embedding-8B-Q4_K_M'
      factory: 'OpenAI-API-Compatible'
      api_key: '$($llama.api_key)'
      base_url: '$($llama.embedding_url)'
authentication:
  disable_password_login: false
  client:
    switch: false
"@|Set-Content (Join-Path $Rag 'conf\local.service_conf.yaml') -Encoding UTF8
 Set-RagEnvironment;Assert-TikaRuntime;& $Python -c 'import cv2, elasticsearch, flask, minio, peewee, quart, valkey';if($LASTEXITCODE -ne 0){throw 'RAGFlow imports failed'};Write-Connection ([ordered]@{schema=1;module='ragflow';url="http://127.0.0.1:$Port"});Set-Installed;Write-Host '[OK] ragflow installed and configured from module contracts'
}
function Start-Api{Assert-CoreDependencies;Set-RagEnvironment;Assert-TikaRuntime;Start-OwnedProcess 'ragflow-api' $Python @('-m','api.ragflow_server') $Rag;Wait-Healthy 'ragflow-api' {Test-Http "http://127.0.0.1:$Port/api/v1/system/healthz"}}
function Start-Worker{Assert-CoreDependencies;Assert-IngestionDependencies;Set-RagEnvironment;Assert-TikaRuntime;Start-OwnedProcess 'task-executor' $Python @((Join-Path $Rag 'rag\svr\task_executor.py'),'-i',(($env:COMPUTERNAME+'_0')),'-t','common') $Rag;Start-Sleep 2;if(-not(Get-OwnedProcess 'task-executor')){throw 'task executor exited'};Write-Host '[OK] task-executor ready'}
switch($CommandName.ToLowerInvariant()){'install'{Install-Ragflow}'start'{Assert-Installed;if($Target -eq 'worker'){Start-Worker}elseif($Target -eq 'all'){Start-Api;Start-Worker}else{Start-Api}}'stop'{if($Target){Stop-OwnedProcess $Target}else{Stop-OwnedProcess 'task-executor';Stop-OwnedProcess 'ragflow-api'}}'status'{Show-ModuleStatus @('ragflow-api','task-executor')}'verify'{& $Python -c 'import cv2, elasticsearch, quart, valkey';if($LASTEXITCODE -ne 0){throw 'RAG runtime imports failed'};Write-Host '[OK] ragflow verified'}default{Write-Host 'Usage: MODULE.bat install|start [api|worker|all]|stop [service]|status|verify';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
