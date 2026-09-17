[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$Python=Join-Path $PSScriptRoot 'runtime\python\python.exe';$Gateway=Join-Path $PSScriptRoot 'service\ocr_job_gateway.py';$Port=9399;$Secrets=Join-Path $StateRoot 'secrets.json'
function Has-Setting($Object,[string]$Name){return $Object.PSObject.Properties.Name -contains $Name}
function Setting($Object,[string]$Name,$Default){if(Has-Setting $Object $Name){return $Object.PSObject.Properties[$Name].Value};return $Default}
function Default-Secrets{return [ordered]@{token=New-HexSecret 32;gpu_index='auto';prefer_gpu_index=1;ingestion_gpu_index='auto';text_recognition_batch_size=8}}
function Resolve-OcrGpuSetting($s,[string]$Target){
 if($Target -match '^(auto|[0-9]+)$'){return $Target}
 if($Target -eq 'ingestion'){return [string](Setting $s 'ingestion_gpu_index' 'auto')}
 return [string](Setting $s 'gpu_index' 'auto')
}
function Set-OcrEnvironment($s,[string]$Target){
 $env:PATH=(Join-Path $PSScriptRoot 'runtime\vc')+';'+(Split-Path $Python)+';'+$env:SystemRoot+'\System32'
 $env:PADDLE_PDX_DISABLE_DEVICE_FALLBACK='1'
 $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='1'
 $env:PADDLE_PDX_CACHE_HOME=Join-Path $PSScriptRoot 'models'
 $env:LOCAL_OCR_GPU_INDEX=Resolve-OcrGpuSetting $s $Target
 $env:LOCAL_OCR_PREFER_GPU_INDEX=[string](Setting $s 'prefer_gpu_index' 1)
 $env:LOCAL_OCR_TEXT_REC_BATCH_SIZE=[string](Setting $s 'text_recognition_batch_size' 8)
 $env:LOCAL_OCR_TOKEN=$s.token
}
function Install-Ocr{Begin-Install;if(Test-Path $Secrets){$s=Read-Json $Secrets}else{$s=Default-Secrets;Write-JsonAtomic $Secrets $s};& $Python (Join-Path $PSScriptRoot 'service\test_ocr_job_gateway_contract.py');if($LASTEXITCODE -ne 0){throw 'OCR API contract failed'};Write-Connection ([ordered]@{schema=1;module='paddleocr';url="http://127.0.0.1:$Port";token=$s.token;strict_gpu=$true});Start-Ocr 'install';Stop-OwnedProcess 'paddleocr';Set-Installed;Write-Host '[OK] paddleocr installed; strict GPU pipeline passed'}
function Start-Ocr{param([string]$Target='');$s=Read-Json $Secrets;Set-OcrEnvironment $s $Target;Start-OwnedProcess 'paddleocr' $Python @($Gateway,'--config',(Join-Path $PSScriptRoot 'service\pp-structure-v3-8gb.yaml'),'--model-root',(Join-Path $PSScriptRoot 'models'),'--jobs-root',(Join-Path $DataRoot 'jobs'),'--host','127.0.0.1','--port',$Port,'--token',$s.token) (Join-Path $PSScriptRoot 'service');Wait-Healthy 'paddleocr' {Test-Http "http://127.0.0.1:$Port/health"}}
function Show-OcrDevices{if(Test-Path $Secrets){$s=Read-Json $Secrets}else{$s=Default-Secrets};Set-OcrEnvironment $s 'devices';& $Python $Gateway '--list-devices';exit $LASTEXITCODE}
switch($CommandName.ToLowerInvariant()){'install'{Install-Ocr}'start'{Assert-Installed;Start-Ocr $Target}'stop'{Stop-OwnedProcess 'paddleocr'}'status'{Show-ModuleStatus @('paddleocr')}'devices'{Show-OcrDevices}'verify'{& $Python -c 'import paddle,paddleocr,paddlex';if($LASTEXITCODE -ne 0){throw 'OCR imports failed'};if($Target -eq 'gpu'){Start-Ocr};Write-Host '[OK] paddleocr verified'}default{Write-Host 'Usage: MODULE.bat install|start [ingestion|auto|gpu-index]|stop|status|devices|verify [gpu]';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
