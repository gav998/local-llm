[CmdletBinding()]param([string]$ModuleRoot,[string]$PayloadRoot,[string]$SourceRoot,[string]$SevenZip)
$ErrorActionPreference='Stop';$Node=Join-Path $PayloadRoot 'build\node';$Source=Join-Path $PayloadRoot 'build\ragflow\web';$Log=Join-Path $SourceRoot 'build-web.log';$env:PATH=$Node+';'+$env:PATH;$env:npm_config_cache=Join-Path $SourceRoot 'npm-cache';$env:NODE_OPTIONS='--max-old-space-size=6144';$env:VITE_BUILD_SOURCEMAP='false';$env:VITE_MINIFY='esbuild'
Push-Location $Source
try{& (Join-Path $Node 'npm.cmd') ci --no-audit --no-fund *>>$Log;if($LASTEXITCODE -ne 0){throw 'npm ci failed'};& (Join-Path $Node 'npm.cmd') run build *>>$Log;if($LASTEXITCODE -ne 0){throw 'web build failed'}}finally{Pop-Location}
if(-not(Test-Path (Join-Path $Source 'dist\index.html'))){throw 'Web dist is missing index.html'};Move-Item (Join-Path $Source 'dist') (Join-Path $PayloadRoot 'web');Remove-Item (Join-Path $PayloadRoot 'build') -Recurse -Force
