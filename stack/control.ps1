[CmdletBinding()]param([string]$CommandName='help',[string]$Profile='')
$ErrorActionPreference='Stop';$Root=Split-Path -Parent $PSScriptRoot;$Modules=Join-Path $Root 'modules'
$StackLogRoot=Join-Path $PSScriptRoot 'logs';New-Item -ItemType Directory -Path $StackLogRoot -Force|Out-Null;$StackLog=Join-Path $StackLogRoot 'control.log';$TranscriptStarted=$false
try{Start-Transcript -Path $StackLog -Append -Force|Out-Null;$TranscriptStarted=$true}catch{Write-Warning "Cannot start stack transcript: $($_.Exception.Message)"}
$Order=@('mysql','elasticsearch','silo','valkey','llama-cpp','paddleocr','ragflow','web')
function ModuleBat([string]$Name){$p=Join-Path $Modules "$Name\MODULE.bat";if(-not(Test-Path $p -PathType Leaf)){throw "Module is not extracted: $Name ($p)"};return $p}
function Run([string]$Name,[string]$Command,[string]$Target=''){Write-Host "`n=== $Name : $Command $Target ===" -ForegroundColor Cyan;& (ModuleBat $Name) $Command $Target;if($LASTEXITCODE -ne 0){throw "$Name $Command failed with exit code $LASTEXITCODE"}}
function Assert-Deployment{if(Test-Path -LiteralPath (Join-Path $Root 'PREPARE-STACK.bat') -PathType Leaf){throw 'Run LOCAL-LLM.bat only from the extracted offline deployment, not from the source tree.'}}
function Start-Data{foreach($m in @('mysql','elasticsearch','silo','valkey')){Run $m 'start'}}
function Start-Core{Run 'ragflow' 'stop' 'task-executor';Run 'paddleocr' 'stop';Run 'llama-cpp' 'stop';Start-Data;Run 'ragflow' 'start' 'api';Run 'web' 'start'}
function Start-IngestionProfile([string]$OcrTarget,[string]$Label){Run 'ragflow' 'stop' 'task-executor';Run 'llama-cpp' 'stop' 'chat';Run 'llama-cpp' 'stop' 'embedding';try{Start-Data;Run 'llama-cpp' 'start' 'ingestion';Run 'paddleocr' 'start' $OcrTarget;Run 'ragflow' 'start' 'api';Run 'ragflow' 'start' 'worker';Run 'web' 'start'}catch{Run 'ragflow' 'stop' 'task-executor';Run 'paddleocr' 'stop';Run 'llama-cpp' 'stop' 'embedding';throw};Write-Host "`n[OK] $Label ready: http://127.0.0.1:9388" -ForegroundColor Green}
function Start-Ingestion{Start-IngestionProfile 'ingestion' 'ingestion profile'}
function Start-IngestionCpu{Start-IngestionProfile 'cpu' 'ingestion CPU comparison profile'}
function Start-Chat{Run 'ragflow' 'stop' 'task-executor';Run 'paddleocr' 'stop';Run 'llama-cpp' 'stop' 'embedding';try{Start-Data;Run 'llama-cpp' 'start' 'embedding';Run 'llama-cpp' 'start' 'chat';Run 'ragflow' 'start' 'api';Run 'web' 'start'}catch{Run 'llama-cpp' 'stop';throw};Write-Host '`n[OK] chat profile ready: http://127.0.0.1:9388' -ForegroundColor Green}
function Start-PaddleOcr{foreach($m in @('web','ragflow','llama-cpp','valkey','silo','elasticsearch','mysql')){Run $m 'stop'};try{Run 'paddleocr' 'workbench'}catch{Run 'paddleocr' 'stop';throw};Write-Host "[OK] standalone PaddleOCR API: http://127.0.0.1:9399" -ForegroundColor Green;Write-Host '[OK] document workbench: http://127.0.0.1:9400' -ForegroundColor Green}
function Stop-All{foreach($m in @('web','ragflow','paddleocr','llama-cpp','valkey','silo','elasticsearch','mysql')){try{Run $m 'stop'}catch{Write-Warning $_}}}
try{switch($CommandName.ToLowerInvariant()){
 'install'{Assert-Deployment;foreach($m in $Order){Run $m 'install'};Write-Host '`n[OK] every independent module is installed' -ForegroundColor Green}
 'start'{Assert-Deployment;switch($Profile){'ingestion'{Start-Ingestion}'ingestion-cpu'{Start-IngestionCpu}'chat'{Start-Chat}'paddleocr'{Start-PaddleOcr}'core'{Start-Core;Write-Host '[OK] core profile ready: http://127.0.0.1:9388' -ForegroundColor Green}default{if([string]::IsNullOrWhiteSpace($Profile)){Start-Core;Write-Host '[OK] core profile ready: http://127.0.0.1:9388' -ForegroundColor Green}else{throw "Unknown start profile '$Profile'. Expected core, paddleocr, ingestion, ingestion-cpu or chat."}}}}
 'stop'{Stop-All}
 'status'{Assert-Deployment;foreach($m in $Order){Run $m 'status'}}
 'verify'{Assert-Deployment;foreach($m in $Order){Run $m 'verify'}}
 'devices'{Assert-Deployment;Run 'llama-cpp' 'devices';Run 'paddleocr' 'devices'}
 default{Write-Host @'
Usage:
  LOCAL-LLM.bat install
  LOCAL-LLM.bat start [core|paddleocr|ingestion|ingestion-cpu|chat]
  LOCAL-LLM.bat stop
  LOCAL-LLM.bat status
  LOCAL-LLM.bat verify
  LOCAL-LLM.bat devices
'@;if($CommandName -ne 'help' -and $CommandName){exit 2}}
}}finally{if($TranscriptStarted){Stop-Transcript|Out-Null}}
