[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$Python=Join-Path $PSScriptRoot 'runtime\python\python.exe';$Service=Join-Path $PSScriptRoot 'service';$App=Join-Path $Service 'digitizer.py';$Port=9400
$ModulesRoot=Split-Path -Parent $PSScriptRoot;$PaddleConnection=Join-Path $ModulesRoot 'paddleocr\state\connection.json';$LlamaConnection=Join-Path $ModulesRoot 'llama-cpp\state\connection.json'
function Read-Dependencies{$ocr=Read-Json $PaddleConnection;$llama=Read-Json $LlamaConnection;if(-not $ocr.url -or -not $ocr.token){throw 'PaddleOCR connection contract is incomplete'};if(-not $llama.chat_url){throw 'llama.cpp connection contract is incomplete'};return @($ocr,$llama)}
function Install-Digitizer{Begin-Install;& $Python (Join-Path $Service 'test_digitizer.py');if($LASTEXITCODE -ne 0){throw 'Digitizer contract tests failed'};Write-Connection ([ordered]@{schema=1;module='digitizer';url="http://127.0.0.1:$Port"});Set-Installed;Write-Host '[OK] digitizer installed'}
function Start-Digitizer{Assert-Installed;$connections=Read-Dependencies;$ocr=$connections[0];$llama=$connections[1];if(-not(Test-Http "$($ocr.url)/health")){throw "PaddleOCR API is not ready: $($ocr.url)"};Start-OwnedProcess 'digitizer' $Python @($App,'--host','127.0.0.1','--port',$Port,'--data-root',$DataRoot,'--paddle-url',$ocr.url,'--paddle-token',$ocr.token,'--llama-url',$llama.chat_url) $Service;Wait-Healthy 'digitizer' {Test-Http "http://127.0.0.1:$Port/health"};try{Start-Process "http://127.0.0.1:$Port"}catch{Write-Warning "Open http://127.0.0.1:$Port"};Write-Host "[OK] digitizer: http://127.0.0.1:$Port" -ForegroundColor Green}
switch($CommandName.ToLowerInvariant()){
 'install'{Install-Digitizer}
 'start'{Start-Digitizer}
 'stop'{Stop-OwnedProcess 'digitizer'}
 'status'{Show-ModuleStatus @('digitizer')}
 'verify'{& $Python (Join-Path $Service 'test_digitizer.py');if($LASTEXITCODE -ne 0){throw 'Digitizer verification failed'};Write-Host '[OK] digitizer verified'}
 default{Write-Host 'Usage: MODULE.bat install|start|stop|status|verify';if($CommandName -ne 'help' -and $CommandName){exit 2}}
}
