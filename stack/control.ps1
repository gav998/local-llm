[CmdletBinding()]param([string]$CommandName='help',[string]$Profile='')
$ErrorActionPreference='Stop';$Root=Split-Path -Parent $PSScriptRoot;$Modules=Join-Path $Root 'modules'
$Order=@('mysql','elasticsearch','silo','valkey','llama-cpp','paddleocr','ragflow','web')
function ModuleBat([string]$Name){$p=Join-Path $Modules "$Name\MODULE.bat";if(-not(Test-Path $p -PathType Leaf)){throw "Module is not extracted: $Name ($p)"};return $p}
function Run([string]$Name,[string]$Command,[string]$Target=''){Write-Host "`n=== $Name : $Command $Target ===" -ForegroundColor Cyan;& (ModuleBat $Name) $Command $Target;if($LASTEXITCODE -ne 0){throw "$Name $Command failed with exit code $LASTEXITCODE"}}
function Start-Data{foreach($m in @('mysql','elasticsearch','silo','valkey')){Run $m 'start'}}
function Start-Core{Run 'ragflow' 'stop' 'task-executor';Run 'paddleocr' 'stop';Run 'llama-cpp' 'stop';Start-Data;Run 'ragflow' 'start' 'api';Run 'web' 'start'}
function Start-Ingestion{Run 'ragflow' 'stop' 'task-executor';Run 'llama-cpp' 'stop' 'chat';Run 'llama-cpp' 'stop' 'embedding';try{Start-Data;Run 'llama-cpp' 'start' 'ingestion';Run 'paddleocr' 'start';Run 'ragflow' 'start' 'api';Run 'ragflow' 'start' 'worker';Run 'web' 'start'}catch{Run 'ragflow' 'stop' 'task-executor';Run 'paddleocr' 'stop';Run 'llama-cpp' 'stop' 'embedding';throw};Write-Host '`n[OK] ingestion profile ready: http://127.0.0.1:9388' -ForegroundColor Green}
function Start-Chat{Run 'ragflow' 'stop' 'task-executor';Run 'paddleocr' 'stop';Run 'llama-cpp' 'stop' 'embedding';try{Start-Data;Run 'llama-cpp' 'start' 'embedding';Run 'llama-cpp' 'start' 'chat';Run 'ragflow' 'start' 'api';Run 'web' 'start'}catch{Run 'llama-cpp' 'stop';throw};Write-Host '`n[OK] chat profile ready: http://127.0.0.1:9388' -ForegroundColor Green}
function Stop-All{foreach($m in @('web','ragflow','paddleocr','llama-cpp','valkey','silo','elasticsearch','mysql')){try{Run $m 'stop'}catch{Write-Warning $_}}}
switch($CommandName.ToLowerInvariant()){
 'install'{foreach($m in $Order){Run $m 'install'};Write-Host '`n[OK] every independent module is installed' -ForegroundColor Green}
 'start'{switch($Profile){'ingestion'{Start-Ingestion}'chat'{Start-Chat}default{Start-Core;Write-Host '`n[OK] core profile ready: http://127.0.0.1:9388' -ForegroundColor Green}}}
 'stop'{Stop-All}
 'status'{foreach($m in $Order){Run $m 'status'}}
 'verify'{foreach($m in $Order){Run $m 'verify'}}
 'devices'{Run 'llama-cpp' 'devices'}
 default{Write-Host @'
Usage:
  LOCAL-LLM.bat install
  LOCAL-LLM.bat start [core|ingestion|chat]
  LOCAL-LLM.bat stop
  LOCAL-LLM.bat status
  LOCAL-LLM.bat verify
  LOCAL-LLM.bat devices
'@;if($CommandName -ne 'help' -and $CommandName){exit 2}}
}
