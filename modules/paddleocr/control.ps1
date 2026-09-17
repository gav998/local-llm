[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$Python=Join-Path $PSScriptRoot 'runtime\python\python.exe';$Gateway=Join-Path $PSScriptRoot 'service\ocr_job_gateway.py';$Port=9399;$Secrets=Join-Path $StateRoot 'secrets.json'
function Has-Setting($Object,[string]$Name){return $Object.PSObject.Properties.Name -contains $Name}
function Setting($Object,[string]$Name,$Default){if(Has-Setting $Object $Name){return $Object.PSObject.Properties[$Name].Value};return $Default}
function Default-Secrets{return [ordered]@{token=New-HexSecret 32;gpu_index='auto';prefer_gpu_index=1;ingestion_gpu_index='auto';text_recognition_batch_size=8}}
function Sync-OcrServiceSource{
 $SrcDir=Join-Path $PSScriptRoot 'src';$SvcDir=Join-Path $PSScriptRoot 'service'
 if(-not(Test-Path $SrcDir -PathType Container)){return}
 New-Item -ItemType Directory -Path $SvcDir -Force|Out-Null
 foreach($Name in @('ocr_job_gateway.py','test_ocr_job_gateway_contract.py','pp-structure-v3-8gb.yaml')){
  $Src=Join-Path $SrcDir $Name
  if(Test-Path $Src -PathType Leaf){Copy-Item -LiteralPath $Src -Destination (Join-Path $SvcDir $Name) -Force}
 }
}
function Resolve-OcrGpuSetting($s,[string]$Target){
 if($Target -match '^(auto|[0-9]+)$'){return $Target}
 if($Target -eq 'ingestion'){return [string](Setting $s 'ingestion_gpu_index' 'auto')}
 return [string](Setting $s 'gpu_index' 'auto')
}
function Resolve-OcrRequestedDevice($s,[string]$Target){
 if($Target -eq 'cpu'){return 'cpu'}
 return Resolve-OcrGpuSetting $s $Target
}
function Resolve-OcrGpuRuntimeIndex($s,[string]$Target){
 $Requested=(Resolve-OcrRequestedDevice $s $Target).Trim().ToLowerInvariant()
 if($Requested -eq 'cpu'){return 'cpu'}
 if($Requested -match '^[0-9]+$'){return $Requested}
 if($Requested -ne 'auto'){throw "OCR GPU index must be 'auto' or a non-negative integer, got '$Requested'"}
 $Code=@'
import os

import paddle

def parse_non_negative(value, name):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a non-negative integer, got {value!r}") from exc
    if parsed < 0:
        raise SystemExit(f"{name} must be a non-negative integer, got {value!r}")
    return parsed

if not paddle.is_compiled_with_cuda():
    raise SystemExit("PaddlePaddle runtime is not compiled with CUDA")
count = paddle.device.cuda.device_count()
if count < 1:
    raise SystemExit("PaddlePaddle sees no CUDA devices")
preferred = parse_non_negative(os.environ.get("LOCAL_OCR_PREFER_GPU_INDEX", "1").strip(), "LOCAL_OCR_PREFER_GPU_INDEX")
print(preferred if preferred < count else 0)
'@
 $Resolved=($Code | & $Python - | Select-Object -Last 1)
 if($LASTEXITCODE -ne 0){throw "Cannot resolve OCR GPU index from '$Requested'"}
 $ResolvedText=([string]$Resolved).Trim()
 if($ResolvedText -notmatch '^[0-9]+$'){throw "Cannot resolve OCR GPU index from '$Requested': $ResolvedText"}
 return $ResolvedText
}
function Set-OcrEnvironment($s,[string]$Target){
 $env:PATH=(Join-Path $PSScriptRoot 'runtime\vc')+';'+(Split-Path $Python)+';'+$env:SystemRoot+'\System32'
 $env:PADDLE_PDX_DISABLE_DEVICE_FALLBACK='1'
 $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='1'
 $env:PADDLE_PDX_CACHE_HOME=Join-Path $PSScriptRoot 'models'
 $env:LOCAL_OCR_PREFER_GPU_INDEX=[string](Setting $s 'prefer_gpu_index' 1)
 $env:LOCAL_OCR_REQUESTED_DEVICE=Resolve-OcrRequestedDevice $s $Target
 $env:LOCAL_OCR_REQUESTED_GPU_INDEX=$env:LOCAL_OCR_REQUESTED_DEVICE
 $env:LOCAL_OCR_GPU_INDEX=Resolve-OcrGpuRuntimeIndex $s $Target
 $env:LOCAL_OCR_DEVICE=$env:LOCAL_OCR_GPU_INDEX
 $env:LOCAL_OCR_TEXT_REC_BATCH_SIZE=[string](Setting $s 'text_recognition_batch_size' 8)
 $env:LOCAL_OCR_TOKEN=$s.token
}
function Install-Ocr{Begin-Install;Sync-OcrServiceSource;if(Test-Path $Secrets){$s=Read-Json $Secrets}else{$s=Default-Secrets;Write-JsonAtomic $Secrets $s};& $Python (Join-Path $PSScriptRoot 'service\test_ocr_job_gateway_contract.py');if($LASTEXITCODE -ne 0){throw 'OCR API contract failed'};Write-Connection ([ordered]@{schema=1;module='paddleocr';url="http://127.0.0.1:$Port";token=$s.token;strict_gpu=$true});Start-Ocr 'install';Stop-OwnedProcess 'paddleocr';Set-Installed;Write-Host '[OK] paddleocr installed; strict GPU pipeline passed'}
function Start-Ocr{param([string]$Target='');Sync-OcrServiceSource;$s=Read-Json $Secrets;Set-OcrEnvironment $s $Target;Start-OwnedProcess 'paddleocr' $Python @($Gateway,'--config',(Join-Path $PSScriptRoot 'service\pp-structure-v3-8gb.yaml'),'--model-root',(Join-Path $PSScriptRoot 'models'),'--jobs-root',(Join-Path $DataRoot 'jobs'),'--host','127.0.0.1','--port',$Port,'--token',$s.token) (Join-Path $PSScriptRoot 'service');Wait-Healthy 'paddleocr' {Test-Http "http://127.0.0.1:$Port/health"}}
function Show-OcrDevices{if(Test-Path $Secrets){$s=Read-Json $Secrets}else{$s=Default-Secrets};Set-OcrEnvironment $s 'ingestion';$Code=@'
import json
import os

import paddle

def parse_non_negative(value, name):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a non-negative integer, got {value!r}") from exc
    if parsed < 0:
        raise SystemExit(f"{name} must be a non-negative integer, got {value!r}")
    return parsed

def resolve_gpu_index(count):
    requested = os.environ.get("LOCAL_OCR_REQUESTED_DEVICE", os.environ.get("LOCAL_OCR_REQUESTED_GPU_INDEX", os.environ.get("LOCAL_OCR_GPU_INDEX", "auto"))).strip().lower() or "auto"
    if requested == "cpu":
        return None, "cpu-explicit"
    if requested == "auto":
        preferred = parse_non_negative(os.environ.get("LOCAL_OCR_PREFER_GPU_INDEX", "1").strip(), "LOCAL_OCR_PREFER_GPU_INDEX")
        if preferred < count:
            return preferred, f"auto-prefer-{preferred}"
        return 0, "auto-fallback-0"
    selected = parse_non_negative(requested, "LOCAL_OCR_GPU_INDEX")
    return selected, "explicit"

compiled = bool(paddle.is_compiled_with_cuda())
count = paddle.device.cuda.device_count() if compiled else 0
selected, selection = resolve_gpu_index(count) if count else (None, "no-cuda")
report = {
    "paddle_version": paddle.__version__,
    "compiled_with_cuda": compiled,
    "paddle_cuda_version": str(paddle.version.cuda),
    "cuda_device_count": count,
    "requested_gpu": os.environ.get("LOCAL_OCR_REQUESTED_GPU_INDEX", os.environ.get("LOCAL_OCR_GPU_INDEX", "auto")),
    "prefer_gpu_index": os.environ.get("LOCAL_OCR_PREFER_GPU_INDEX", "1"),
    "resolved_gpu_index": selected,
    "gpu_selection": selection,
    "devices": [],
}
for index in range(count):
    try:
        capability = paddle.device.cuda.get_device_capability(index)
    except Exception as exc:
        capability = f"error: {exc}"
    report["devices"].append({"index": index, "capability": capability})
print(json.dumps(report, ensure_ascii=False, indent=2))
'@;$Code | & $Python -;exit $LASTEXITCODE}
switch($CommandName.ToLowerInvariant()){'install'{Install-Ocr}'start'{Assert-Installed;Start-Ocr $Target}'stop'{Stop-OwnedProcess 'paddleocr'}'status'{Show-ModuleStatus @('paddleocr')}'devices'{Show-OcrDevices}'verify'{& $Python -c 'import paddle,paddleocr,paddlex';if($LASTEXITCODE -ne 0){throw 'OCR imports failed'};if($Target -eq 'gpu'){Start-Ocr};Write-Host '[OK] paddleocr verified'}default{Write-Host 'Usage: MODULE.bat install|start [ingestion|cpu|auto|gpu-index]|stop|status|devices|verify [gpu]';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
