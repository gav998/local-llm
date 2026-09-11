[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$Python=Join-Path $PSScriptRoot 'runtime\python\python.exe';$Rag=Join-Path $PSScriptRoot 'ragflow';$Port=9380;$ModulesRoot=Split-Path -Parent $PSScriptRoot;$Secrets=Join-Path $StateRoot 'secrets.json'
function Verify-Payload{Test-Payload;$env:PATH=(Join-Path $PSScriptRoot 'runtime\vc')+';'+(Split-Path $Python)+';'+$env:SystemRoot+'\System32';& $Python -c 'import cv2, elasticsearch, flask, minio, peewee, quart, valkey; print("RAG archive imports OK")';if($LASTEXITCODE -ne 0){throw 'RAG archive import probe failed'}}
function Connection([string]$Name){Read-Json (Join-Path $ModulesRoot "$Name\state\connection.json")}
function Set-RagEnvironment{
 $ocr=Connection 'paddleocr';$env:PYTHONPATH=$Rag;$env:PATH=(Join-Path $PSScriptRoot 'runtime\vc')+';'+(Split-Path $Python)+';'+$env:SystemRoot+'\System32';$env:NLTK_DATA=Join-Path $PSScriptRoot 'assets\nltk';$env:TIKTOKEN_CACHE_DIR=$Rag;$env:TIKA_SERVER_JAR='file:///'+((Join-Path $Rag 'tika-server-standard-3.3.0.jar').Replace('\','/'));$env:PADDLEOCR_BASE_URL=$ocr.url;$env:PADDLEOCR_API_URL=$ocr.url;$env:PADDLEOCR_ACCESS_TOKEN=$ocr.token;$env:HF_HUB_OFFLINE='1';$env:TRANSFORMERS_OFFLINE='1'
}
function Assert-CoreDependencies{$mysql=Connection 'mysql';$es=Connection 'elasticsearch';$silo=Connection 'silo';$valkey=Connection 'valkey';if(-not(Test-Tcp ([int]$mysql.port))){throw 'MySQL endpoint is not ready'};if(-not(Test-Http $es.url)){throw 'Elasticsearch endpoint is not ready'};if(-not(Test-Http ("http://$($silo.endpoint)/minio/health/ready"))){throw 'Silo endpoint is not ready'};if(-not(Test-Tcp ([int]$valkey.port))){throw 'Valkey endpoint is not ready'}}
function Assert-IngestionDependencies{$llama=Connection 'llama-cpp';$ocr=Connection 'paddleocr';if(-not(Test-Http ($llama.embedding_url.Replace('/v1','/health')))){throw 'Embedding endpoint is not ready'};if(-not(Test-Http ($ocr.url+'/health'))){throw 'PaddleOCR endpoint is not ready'}}
function Install-Ragflow{
 Begin-Install;Test-Payload;$mysql=Connection 'mysql';$es=Connection 'elasticsearch';$silo=Connection 'silo';$valkey=Connection 'valkey';$llama=Connection 'llama-cpp';$ocr=Connection 'paddleocr';if(Test-Path $Secrets){$secret=Read-Json $Secrets}else{$secret=[ordered]@{secret_key=New-HexSecret 32};Write-JsonAtomic $Secrets $secret}
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
 Set-RagEnvironment;& $Python -c 'import cv2, elasticsearch, flask, minio, peewee, quart, valkey; print("RAG imports OK")';if($LASTEXITCODE -ne 0){throw 'RAGFlow imports failed'};Write-Connection ([ordered]@{schema=1;module='ragflow';url="http://127.0.0.1:$Port"});Set-Installed;Write-Host '[OK] ragflow installed and configured from module contracts'
}
function Start-Api{Assert-CoreDependencies;Set-RagEnvironment;Start-OwnedProcess 'ragflow-api' $Python @('-m','api.ragflow_server') $Rag;Wait-Healthy 'ragflow-api' {Test-Http "http://127.0.0.1:$Port/api/v1/system/healthz"}}
function Start-Worker{Assert-CoreDependencies;Assert-IngestionDependencies;Set-RagEnvironment;Start-OwnedProcess 'task-executor' $Python @((Join-Path $Rag 'rag\svr\task_executor.py'),'-i',(($env:COMPUTERNAME+'_0')),'-t','common') $Rag;Start-Sleep 2;if(-not(Get-OwnedProcess 'task-executor')){throw 'task executor exited'};Write-Host '[OK] task-executor ready'}
switch($CommandName.ToLowerInvariant()){'install'{Install-Ragflow}'start'{Assert-Installed;if($Target -eq 'worker'){Start-Worker}elseif($Target -eq 'all'){Start-Api;Start-Worker}else{Start-Api}}'stop'{if($Target){Stop-OwnedProcess $Target}else{Stop-OwnedProcess 'task-executor';Stop-OwnedProcess 'ragflow-api'}}'status'{Show-ModuleStatus @('ragflow-api','task-executor')}'verify'{Test-Payload;& $Python -c 'import cv2, elasticsearch, quart, valkey; print("RAG runtime OK")';if($LASTEXITCODE -ne 0){throw 'RAG runtime imports failed'};Write-Host '[OK] ragflow verified'}'verify-payload'{Verify-Payload}default{Write-Host 'Usage: MODULE.bat install|start [api|worker|all]|stop [service]|status|verify';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
