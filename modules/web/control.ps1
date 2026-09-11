[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$WebPort=9388;$AdminPort=2019;$Exe=Join-Path $PSScriptRoot 'runtime\caddy.exe';$ModulesRoot=Split-Path -Parent $PSScriptRoot;$Caddyfile=Join-Path $ConfigRoot 'Caddyfile'
function Verify-Payload{Test-Payload;& $Exe version;if($LASTEXITCODE -ne 0){throw 'Caddy version probe failed'};if(-not(Test-Path (Join-Path $PSScriptRoot 'web\index.html'))){throw 'Web index is missing'}}
function Write-WebConfig{$rag=Read-Json (Join-Path $ModulesRoot 'ragflow\state\connection.json');@"
{
    admin 127.0.0.1:$AdminPort
    auto_https off
}
http://127.0.0.1:$WebPort {
    root * "$((Join-Path $PSScriptRoot 'web').Replace('\','/'))"
    encode gzip
    handle /v1/* { reverse_proxy $($rag.url.Replace('http://','')) }
    handle /api/* { reverse_proxy $($rag.url.Replace('http://','')) }
    handle {
        try_files {path} /index.html
        file_server
    }
}
"@|Set-Content $Caddyfile -Encoding UTF8;& $Exe validate --config $Caddyfile --adapter caddyfile;if($LASTEXITCODE -ne 0){throw 'Caddy configuration validation failed'}}
function Install-Web{Begin-Install;Test-Payload;Write-WebConfig;Write-Connection ([ordered]@{schema=1;module='web';url="http://127.0.0.1:$WebPort"});Set-Installed;Write-Host '[OK] web installed'}
function Start-Web{Write-WebConfig;$rag=Read-Json (Join-Path $ModulesRoot 'ragflow\state\connection.json');if(-not(Test-Http ($rag.url+'/api/v1/system/healthz'))){throw 'RAGFlow API is not ready'};Start-OwnedProcess 'web' $Exe @('run','--config',$Caddyfile,'--adapter','caddyfile') (Split-Path $Exe);Wait-Healthy 'web' {Test-Http "http://127.0.0.1:$WebPort/"}}
function Stop-Web{if(Get-OwnedProcess 'web'){& $Exe stop --address "127.0.0.1:$AdminPort" 2>$null|Out-Null;Start-Sleep 1};if(Get-OwnedProcess 'web'){Stop-OwnedProcess 'web'}else{Remove-Item (Join-Path $StateRoot 'process-web.json') -Force -ErrorAction SilentlyContinue;Write-Host '[OK] web stopped'}}
switch($CommandName.ToLowerInvariant()){'install'{Install-Web}'start'{Assert-Installed;Start-Web}'stop'{Stop-Web}'status'{Show-ModuleStatus @('web')}'verify'{Test-Payload;if((Get-OwnedProcess 'web')-and -not(Test-Http "http://127.0.0.1:$WebPort/")){throw 'Web health failed'};Write-Host '[OK] web verified'}'verify-payload'{Verify-Payload}default{Write-Host 'Usage: MODULE.bat install|start|stop|status|verify';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
