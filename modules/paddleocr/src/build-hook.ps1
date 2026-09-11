[CmdletBinding()]param([string]$ModuleRoot,[string]$PayloadRoot,[string]$SourceRoot,[string]$SevenZip)
$ErrorActionPreference='Stop';$Python=Join-Path $PayloadRoot 'runtime\python\python.exe';$Uv=Join-Path $PayloadRoot 'build\uv\uv.exe';$Wheel=Join-Path $PayloadRoot 'vendor\paddlepaddle_gpu-3.3.1-cp311-cp311-win_amd64.whl';$Wheelhouse=Join-Path $SourceRoot 'wheelhouse';$Log=Join-Path $SourceRoot 'build-ocr.log'
$env:PIP_CACHE_DIR=Join-Path $SourceRoot 'cache\pip';$env:UV_CACHE_DIR=Join-Path $SourceRoot 'cache\uv';$env:UV_NO_CONFIG='1';$env:UV_LINK_MODE='copy';$env:PYTHONNOUSERSITE='1'
New-Item -ItemType Directory -Path $Wheelhouse -Force|Out-Null
foreach($model in @('PP-DocLayout-L','PP-DocBlockLayout','PP-OCRv6_medium_det','eslav_PP-OCRv5_mobile_rec','SLANet_plus')){foreach($file in @('inference.json','inference.yml','inference.pdiparams')){if(-not(Test-Path (Join-Path $PayloadRoot "models\$model\$file") -PathType Leaf)){throw "Incomplete Paddle model: $model/$file"}}}
& $Uv pip install --python $Python 'pip==26.2.1' *>>$Log;if($LASTEXITCODE -ne 0){throw 'pip bootstrap failed'}
Copy-Item $Wheel -Destination $Wheelhouse -Force
& $Python -m pip download --dest $Wheelhouse --find-links $Wheelhouse --only-binary=:all: $Wheel --requirement (Join-Path $PSScriptRoot 'requirements-ocr.txt') *>>$Log;if($LASTEXITCODE -ne 0){throw 'OCR wheelhouse resolution failed'}
& $Uv pip install --python $Python --no-index --find-links $Wheelhouse $Wheel --requirements (Join-Path $PSScriptRoot 'requirements-ocr.txt') *>>$Log;if($LASTEXITCODE -ne 0){throw 'OCR installation failed'}
$Vc=Join-Path $PayloadRoot 'vendor\VC_redist.x64.exe';$Stage=Join-Path $PayloadRoot 'vc-stage';New-Item -ItemType Directory -Path "$Stage\parts","$Stage\payload","$Stage\dlls" -Force|Out-Null;& $SevenZip x -y -t# "-o$Stage\parts" $Vc|Out-Null;& $SevenZip x -y "-o$Stage\payload" "$Stage\parts\4.cab"|Out-Null;& $SevenZip x -y "-o$Stage\dlls" "$Stage\payload\a12"|Out-Null
$DllRoot=Join-Path $PayloadRoot 'runtime\vc';New-Item -ItemType Directory -Path $DllRoot -Force|Out-Null;Get-ChildItem "$Stage\dlls\*_amd64"|ForEach-Object{$name=$_.Name.Replace('_amd64','');Copy-Item $_.FullName (Join-Path $DllRoot $name) -Force;Copy-Item $_.FullName (Join-Path (Split-Path $Python) $name) -Force};Remove-Item $Stage -Recurse -Force
& $Python (Join-Path $PSScriptRoot 'sanitize_python_runtime.py') --runtime (Split-Path $Python) --record (Join-Path $PayloadRoot 'runtime\python-sanitize.json') *>>$Log;if($LASTEXITCODE -ne 0){throw 'Python sanitization failed'}
& $Uv pip check --python $Python *>>$Log;if($LASTEXITCODE -ne 0){throw 'OCR pip check failed'}
& $Uv pip freeze --python $Python | Set-Content (Join-Path $PayloadRoot 'runtime\ocr-freeze.txt') -Encoding UTF8;if($LASTEXITCODE -ne 0){throw 'OCR freeze failed'}
New-Item -ItemType Directory -Path (Join-Path $PayloadRoot 'service') -Force|Out-Null
Copy-Item (Join-Path $PSScriptRoot 'ocr_job_gateway.py'),(Join-Path $PSScriptRoot 'test_ocr_job_gateway_contract.py'),(Join-Path $PSScriptRoot 'pp-structure-v3-8gb.yaml') -Destination (Join-Path $PayloadRoot 'service') -Force
& $Python -c "import paddle,paddleocr,paddlex; print(paddle.__version__,paddleocr.__version__,paddlex.__version__)" *>>$Log;if($LASTEXITCODE -ne 0){throw 'OCR import smoke failed'}
& $Python (Join-Path $PSScriptRoot 'test_ocr_job_gateway_contract.py') *>>$Log;if($LASTEXITCODE -ne 0){throw 'OCR API contract failed'}
Remove-Item (Join-Path $PayloadRoot 'build') -Recurse -Force;Remove-Item (Join-Path $PayloadRoot 'vendor') -Recurse -Force
& $Python (Join-Path $PSScriptRoot 'audit_portability.py') --root $PayloadRoot --build-root $ModuleRoot --record (Join-Path $PayloadRoot 'runtime\portability-audit.json') *>>$Log;if($LASTEXITCODE -ne 0){throw 'OCR portability audit failed'}
