[CmdletBinding()]param([string]$ModuleRoot,[string]$PayloadRoot,[string]$SourceRoot,[string]$CacheRoot,[string]$SevenZip)
$ErrorActionPreference='Stop';$Python=Join-Path $PayloadRoot 'runtime\python\python.exe';$Uv=Join-Path $PayloadRoot 'build\uv\uv.exe';$Wheel=Join-Path $PayloadRoot 'vendor\paddlepaddle_gpu-3.3.1-cp311-cp311-win_amd64.whl';$Wheelhouse=Join-Path $CacheRoot 'wheelhouse';$Log=Join-Path $CacheRoot 'build-ocr.log'
$env:PIP_CACHE_DIR=Join-Path $CacheRoot 'cache\pip';$env:UV_CACHE_DIR=Join-Path $CacheRoot 'cache\uv';$env:UV_NO_CONFIG='1';$env:UV_LINK_MODE='copy';$env:PYTHONNOUSERSITE='1'
function Invoke-LoggedNative([string]$Executable,[object[]]$Arguments,[string]$FailureMessage){
    if(-not(Test-Path -LiteralPath $Executable -PathType Leaf)){throw "Native executable is missing: $Executable"}
    $PreviousErrorActionPreference=$ErrorActionPreference
    try{$ErrorActionPreference='Continue';& $Executable @Arguments *>>$Log;$ExitCode=$LASTEXITCODE}
    finally{$ErrorActionPreference=$PreviousErrorActionPreference}
    if($ExitCode -ne 0){throw "$FailureMessage (exit code $ExitCode; see $Log)"}
}
function Export-NativeOutput([string]$Executable,[object[]]$Arguments,[string]$OutputPath,[string]$FailureMessage){
    if(-not(Test-Path -LiteralPath $Executable -PathType Leaf)){throw "Native executable is missing: $Executable"}
    $PreviousErrorActionPreference=$ErrorActionPreference
    try{$ErrorActionPreference='Continue';& $Executable @Arguments 2>>$Log|Set-Content -LiteralPath $OutputPath -Encoding UTF8;$ExitCode=$LASTEXITCODE}
    finally{$ErrorActionPreference=$PreviousErrorActionPreference}
    if($ExitCode -ne 0){throw "$FailureMessage (exit code $ExitCode; see $Log)"}
}
New-Item -ItemType Directory -Path $Wheelhouse -Force|Out-Null
foreach($model in @('PP-DocLayout-L','PP-DocBlockLayout','PP-OCRv6_medium_det','eslav_PP-OCRv5_mobile_rec','SLANet_plus','PP-LCNet_x1_0_doc_ori','UVDoc','PP-LCNet_x1_0_textline_ori','PP-OCRv4_server_seal_det','PP-FormulaNet_plus-S')){foreach($file in @('inference.json','inference.yml','inference.pdiparams')){if(-not(Test-Path (Join-Path $PayloadRoot "models\$model\$file") -PathType Leaf)){throw "Incomplete Paddle model: $model/$file"}}}
foreach($file in @('config.json','inference.yml','model_state.pdparams')){if(-not(Test-Path (Join-Path $PayloadRoot "models\PP-Chart2Table\$file") -PathType Leaf)){throw "Incomplete Paddle model: PP-Chart2Table/$file"}}
Invoke-LoggedNative $Uv @('pip','install','--python',$Python,'pip==26.2.1') 'pip bootstrap failed'
Copy-Item $Wheel -Destination $Wheelhouse -Force
Invoke-LoggedNative $Python @('-m','pip','download','--dest',$Wheelhouse,'--find-links',$Wheelhouse,'--only-binary=:all:',$Wheel,'--requirement',(Join-Path $PSScriptRoot 'requirements-ocr.txt')) 'OCR wheelhouse resolution failed'
Invoke-LoggedNative $Uv @('pip','install','--python',$Python,'--no-index','--find-links',$Wheelhouse,$Wheel,'--requirements',(Join-Path $PSScriptRoot 'requirements-ocr.txt')) 'OCR installation failed'
$Vc=Join-Path $PayloadRoot 'vendor\VC_redist.x64.exe';$Stage=Join-Path $PayloadRoot 'vc-stage';New-Item -ItemType Directory -Path "$Stage\parts","$Stage\payload","$Stage\dlls" -Force|Out-Null;& $SevenZip x -y -t# "-o$Stage\parts" $Vc|Out-Null;& $SevenZip x -y "-o$Stage\payload" "$Stage\parts\4.cab"|Out-Null;& $SevenZip x -y "-o$Stage\dlls" "$Stage\payload\a12"|Out-Null
$DllRoot=Join-Path $PayloadRoot 'runtime\vc';New-Item -ItemType Directory -Path $DllRoot -Force|Out-Null;Get-ChildItem "$Stage\dlls\*_amd64"|ForEach-Object{$name=$_.Name.Replace('_amd64','');Copy-Item $_.FullName (Join-Path $DllRoot $name) -Force;Copy-Item $_.FullName (Join-Path (Split-Path $Python) $name) -Force};Remove-Item $Stage -Recurse -Force
Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'sanitize_python_runtime.py'),'--runtime',(Split-Path $Python),'--record',(Join-Path $PayloadRoot 'runtime\python-sanitize.json')) 'Python sanitization failed'
Invoke-LoggedNative $Uv @('pip','check','--python',$Python) 'OCR pip check failed'
Export-NativeOutput $Uv @('pip','freeze','--python',$Python) (Join-Path $PayloadRoot 'runtime\ocr-freeze.txt') 'OCR freeze failed'
New-Item -ItemType Directory -Path (Join-Path $PayloadRoot 'service') -Force|Out-Null
Copy-Item (Join-Path $PSScriptRoot 'ocr_job_gateway.py'),(Join-Path $PSScriptRoot 'test_ocr_job_gateway_contract.py'),(Join-Path $PSScriptRoot 'pp-structure-v3-8gb.yaml') -Destination (Join-Path $PayloadRoot 'service') -Force
Invoke-LoggedNative $Python @('-c','import paddle,paddleocr,paddlex,pymupdf; print(paddle.__version__,paddleocr.__version__,paddlex.__version__,pymupdf.__version__)') 'OCR import smoke failed'
Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'test_ocr_job_gateway_contract.py')) 'OCR API contract failed'
Remove-Item (Join-Path $PayloadRoot 'build') -Recurse -Force;Remove-Item (Join-Path $PayloadRoot 'vendor') -Recurse -Force
Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'audit_portability.py'),'--root',$PayloadRoot,'--build-root',$ModuleRoot,'--record',(Join-Path $PayloadRoot 'runtime\portability-audit.json')) 'OCR portability audit failed'
