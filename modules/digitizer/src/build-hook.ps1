[CmdletBinding()]param([string]$ModuleRoot,[string]$PayloadRoot,[string]$SourceRoot,[string]$CacheRoot,[string]$SevenZip)
$ErrorActionPreference='Stop';$Python=Join-Path $PayloadRoot 'runtime\python\python.exe';$Uv=Join-Path $PayloadRoot 'build\uv\uv.exe';$Wheelhouse=Join-Path $CacheRoot 'wheelhouse';$Log=Join-Path $CacheRoot 'build-digitizer.log'
$env:PIP_CACHE_DIR=Join-Path $CacheRoot 'cache\pip';$env:UV_CACHE_DIR=Join-Path $CacheRoot 'cache\uv';$env:UV_NO_CONFIG='1';$env:UV_LINK_MODE='copy';$env:PYTHONNOUSERSITE='1'
function Invoke-LoggedNative([string]$Executable,[object[]]$Arguments,[string]$FailureMessage){
 if(-not(Test-Path -LiteralPath $Executable -PathType Leaf)){throw "Native executable is missing: $Executable"}
 $PreviousErrorActionPreference=$ErrorActionPreference
 try{$ErrorActionPreference='Continue';& $Executable @Arguments *>>$Log;$ExitCode=$LASTEXITCODE}
 finally{$ErrorActionPreference=$PreviousErrorActionPreference}
 if($ExitCode -ne 0){throw "$FailureMessage (exit code $ExitCode; see $Log)"}
}
New-Item -ItemType Directory -Path $Wheelhouse -Force|Out-Null
Invoke-LoggedNative $Uv @('pip','install','--python',$Python,'pip==26.2.1') 'pip bootstrap failed'
Invoke-LoggedNative $Python @('-m','pip','download','--dest',$Wheelhouse,'--only-binary=:all:','--requirement',(Join-Path $PSScriptRoot 'requirements-digitizer.txt')) 'Digitizer wheelhouse resolution failed'
Invoke-LoggedNative $Uv @('pip','install','--python',$Python,'--no-index','--find-links',$Wheelhouse,'--requirements',(Join-Path $PSScriptRoot 'requirements-digitizer.txt')) 'Digitizer installation failed'
$Vc=Join-Path $PayloadRoot 'vendor\VC_redist.x64.exe';$Stage=Join-Path $PayloadRoot 'vc-stage';New-Item -ItemType Directory -Path "$Stage\parts","$Stage\payload","$Stage\dlls" -Force|Out-Null;& $SevenZip x -y -t# "-o$Stage\parts" $Vc|Out-Null;& $SevenZip x -y "-o$Stage\payload" "$Stage\parts\4.cab"|Out-Null;& $SevenZip x -y "-o$Stage\dlls" "$Stage\payload\a12"|Out-Null
$DllRoot=Join-Path $PayloadRoot 'runtime\vc';New-Item -ItemType Directory -Path $DllRoot -Force|Out-Null;Get-ChildItem "$Stage\dlls\*_amd64"|ForEach-Object{$name=$_.Name.Replace('_amd64','');Copy-Item $_.FullName (Join-Path $DllRoot $name) -Force;Copy-Item $_.FullName (Join-Path (Split-Path $Python) $name) -Force};Remove-Item $Stage -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $PayloadRoot 'service') -Force|Out-Null
Copy-Item (Join-Path $PSScriptRoot 'digitizer.py'),(Join-Path $PSScriptRoot 'digitizer.html'),(Join-Path $PSScriptRoot 'test_digitizer.py') -Destination (Join-Path $PayloadRoot 'service') -Force
Invoke-LoggedNative $Python @((Join-Path $PSScriptRoot 'test_digitizer.py')) 'Digitizer contract tests failed'
Remove-Item (Join-Path $PayloadRoot 'build') -Recurse -Force;Remove-Item (Join-Path $PayloadRoot 'vendor') -Recurse -Force
