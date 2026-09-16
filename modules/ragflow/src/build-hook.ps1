[CmdletBinding()]param([string]$ModuleRoot,[string]$PayloadRoot,[string]$SourceRoot,[string]$SevenZip)
$ErrorActionPreference='Stop';$CacheRoot=Join-Path $SourceRoot 'ragflow';$Python=Join-Path $PayloadRoot 'runtime\python\python.exe';$Uv=Join-Path $PayloadRoot 'build\uv\uv.exe';$Rag=Join-Path $PayloadRoot 'ragflow';$Wheelhouse=Join-Path $CacheRoot 'wheelhouse';$Log=Join-Path $CacheRoot 'build.log';$Locks=Join-Path $PayloadRoot 'locks'
New-Item -ItemType Directory -Path $Wheelhouse,$Locks -Force|Out-Null;$env:PATH=(Join-Path $PayloadRoot 'build\git\cmd')+';'+(Join-Path $PayloadRoot 'build\git\mingw64\bin')+';'+$env:PATH;$env:PIP_CACHE_DIR=Join-Path $CacheRoot 'cache\pip';$env:UV_CACHE_DIR=Join-Path $CacheRoot 'cache\uv';$env:UV_NO_CONFIG='1';$env:UV_LINK_MODE='copy';$env:PYTHONNOUSERSITE='1'
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
$Upstream=Join-Path $Locks 'ragflow-0.27.1-upstream.txt';$Windows=Join-Path $Locks 'ragflow-0.27.1-windows.txt'
Push-Location $Rag
try{Invoke-LoggedNative $Uv @('export','--frozen','--no-group','test','--no-emit-project','--no-emit-package','numpy','--no-emit-package','xgboost','--no-emit-package','datrie','--no-emit-package','graspologic','--no-emit-package','infinity-emb','--no-emit-package','unclecode-litellm','--no-emit-package','agentrun-mem0ai','--no-header','--no-annotate','--format','requirements-txt','--output-file',$Upstream) 'RAGFlow lock export failed'
Invoke-LoggedNative $Uv @('pip','compile',(Join-Path $Rag 'pyproject.toml'),(Join-Path $PSScriptRoot 'ragflow-windows-additions.txt'),'--python',$Python,'--constraints',$Upstream,'--overrides',(Join-Path $PSScriptRoot 'ragflow-windows-overrides.txt'),'--excludes',(Join-Path $PSScriptRoot 'ragflow-windows-excludes.txt'),'--generate-hashes','--no-header','--no-annotate','--output-file',$Windows) 'RAGFlow Windows lock compile failed'}finally{Pop-Location}
Invoke-LoggedNative $Uv @('pip','install','--python',$Python,'pip==26.2.1') 'pip bootstrap failed'
# Setuptools mirrors absolute source paths below PYTHONPYCACHEPREFIX.  On
# Windows that makes thrift's temporary optimized-pyc path exceed MAX_PATH.
$PreviousPythonPycCachePrefix=$env:PYTHONPYCACHEPREFIX
try{
    Remove-Item Env:PYTHONPYCACHEPREFIX -ErrorAction SilentlyContinue
    Invoke-LoggedNative $Python @('-m','pip','wheel','--wheel-dir',$Wheelhouse,'--no-deps','--require-hashes','--requirement',$Windows) 'RAGFlow wheelhouse build failed'
}finally{
    if($null -eq $PreviousPythonPycCachePrefix){Remove-Item Env:PYTHONPYCACHEPREFIX -ErrorAction SilentlyContinue}else{$env:PYTHONPYCACHEPREFIX=$PreviousPythonPycCachePrefix}
}
Copy-Item (Join-Path $PayloadRoot 'vendor\datrie-0.8.3-cp313-cp313-win_amd64.whl') $Wheelhouse -Force
$WheelLock=Join-Path $Wheelhouse 'requirements.lock';Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'prepare_wheelhouse_lock.py'),'--requirements',$Windows,'--wheelhouse',$Wheelhouse,'--output',$WheelLock) 'Wheelhouse lock failed'
Copy-Item $WheelLock (Join-Path $Locks 'ragflow-wheelhouse.lock') -Force
Invoke-LoggedNative $Uv @('pip','sync','--python',$Python,'--no-index','--find-links',$Wheelhouse,'--require-hashes',$WheelLock) 'RAGFlow wheel sync failed'
Invoke-LoggedNative $Uv @('pip','install','--python',$Python,'--no-index','--no-deps',(Join-Path $Wheelhouse 'datrie-0.8.3-cp313-cp313-win_amd64.whl')) 'datrie install failed'
$Vc=Join-Path $PayloadRoot 'vendor\VC_redist.x64.exe';$Stage=Join-Path $PayloadRoot 'vc-stage';New-Item -ItemType Directory -Path "$Stage\parts","$Stage\payload","$Stage\dlls" -Force|Out-Null;& $SevenZip x -y -t# "-o$Stage\parts" $Vc|Out-Null;& $SevenZip x -y "-o$Stage\payload" "$Stage\parts\4.cab"|Out-Null;& $SevenZip x -y "-o$Stage\dlls" "$Stage\payload\a12"|Out-Null;$DllRoot=Join-Path $PayloadRoot 'runtime\vc';New-Item -ItemType Directory -Path $DllRoot -Force|Out-Null;Get-ChildItem "$Stage\dlls\*_amd64"|ForEach-Object{$n=$_.Name.Replace('_amd64','');Copy-Item $_.FullName (Join-Path $DllRoot $n) -Force;Copy-Item $_.FullName (Join-Path (Split-Path $Python) $n) -Force};Remove-Item $Stage -Recurse -Force
Copy-Item (Join-Path $DllRoot 'vcomp140.dll') (Join-Path (Split-Path $Python) 'Lib\site-packages\xgboost\lib\vcomp140.dll') -Force
Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'prepare_ragflow_windows.py'),'--ragflow-dir',$Rag,'--record',(Join-Path $PayloadRoot 'locks\windows-compat.json')) 'RAGFlow compatibility patch failed'
$Nltk=Join-Path $PayloadRoot 'assets\nltk';Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'prepare_ragflow_assets.py'),'--ragflow-dir',$Rag,'--nltk-dir',$Nltk,'--record',(Join-Path $PayloadRoot 'locks\assets.json'),'--manual-assets-dir',$SourceRoot) 'RAGFlow asset preparation failed'
Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'sanitize_python_runtime.py'),'--runtime',(Split-Path $Python),'--record',(Join-Path $PayloadRoot 'locks\python-sanitize.json')) 'Python sanitization failed'
Invoke-LoggedNative $Uv @('pip','check','--python',$Python) 'RAGFlow pip check failed';Export-NativeOutput $Uv @('pip','freeze','--python',$Python) (Join-Path $Locks 'ragflow-freeze.txt') 'RAGFlow freeze failed';Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'verify_ragflow_runtime.py'),'--ragflow-dir',$Rag) 'RAGFlow runtime verification failed'
Remove-Item (Join-Path $Rag 'web') -Recurse -Force -ErrorAction SilentlyContinue;Remove-Item (Join-Path $PayloadRoot 'build'),(Join-Path $PayloadRoot 'vendor') -Recurse -Force
Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'audit_portability.py'),'--root',$PayloadRoot,'--build-root',$ModuleRoot,'--record',(Join-Path $Locks 'portability-audit.json')) 'RAGFlow portability audit failed'
