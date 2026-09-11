[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$Port=6379;$Runtime=Join-Path $PSScriptRoot 'runtime';$Secrets=Join-Path $StateRoot 'secrets.json';$Conf=Join-Path $ConfigRoot 'valkey.conf'
function Verify-Payload{Test-Payload;& (Join-Path $Runtime 'valkey-server.exe') --version;if($LASTEXITCODE -ne 0){throw 'Valkey version probe failed'}}
function CygPath([string]$p){$n=$p.Replace('\','/');if($n -match '^([A-Za-z]):/(.*)$'){return "/cygdrive/$($Matches[1].ToLower())/$($Matches[2])"};return $n}
function Write-ValkeyConfig($s){@"
bind 127.0.0.1
protected-mode yes
port $Port
requirepass $($s.password)
maxmemory 256mb
maxmemory-policy volatile-lru
dir "$((Join-Path $DataRoot 'store').Replace('\','/'))"
appendonly yes
"@|Set-Content $Conf -Encoding ASCII;New-Item -ItemType Directory -Path (Join-Path $DataRoot 'store') -Force|Out-Null}
function Install-Valkey{Begin-Install;Test-Payload;& (Join-Path $Runtime 'valkey-server.exe') --version;if($LASTEXITCODE -ne 0){throw 'Valkey version probe failed'};if(Test-Path $Secrets){$s=Read-Json $Secrets}else{$s=[ordered]@{password=New-HexSecret 24};Write-JsonAtomic $Secrets $s};Write-ValkeyConfig $s;Write-Connection ([ordered]@{schema=1;module='valkey';host='127.0.0.1';port=$Port;database=1;password=$s.password});Set-Installed;Write-Host '[OK] valkey installed'}
function Probe-Valkey{$s=Read-Json $Secrets;$env:VALKEYCLI_AUTH=$s.password;& (Join-Path $Runtime 'valkey-cli.exe') -h 127.0.0.1 -p $Port ping 2>$null|Out-Null;return $LASTEXITCODE -eq 0}
function Start-Valkey{$s=Read-Json $Secrets;Write-ValkeyConfig $s;$env:VALKEYCLI_AUTH=$s.password;Start-OwnedProcess 'valkey' (Join-Path $Runtime 'valkey-server.exe') @((CygPath $Conf)) $Runtime;Wait-Healthy 'valkey' {Probe-Valkey}}
switch($CommandName.ToLowerInvariant()){'install'{Install-Valkey}'start'{Assert-Installed;Start-Valkey}'stop'{if(Get-OwnedProcess 'valkey'){$s=Read-Json $Secrets;$env:VALKEYCLI_AUTH=$s.password;& (Join-Path $Runtime 'valkey-cli.exe') -h 127.0.0.1 -p $Port shutdown save 2>$null|Out-Null;Start-Sleep 1};if(Get-OwnedProcess 'valkey'){Stop-OwnedProcess 'valkey'}else{Remove-Item (Join-Path $StateRoot 'process-valkey.json') -Force -ErrorAction SilentlyContinue}}'status'{Show-ModuleStatus @('valkey')}'verify'{Test-Payload;if((Get-OwnedProcess 'valkey')-and -not(Probe-Valkey)){throw 'Valkey health failed'};Write-Host '[OK] valkey verified'}'verify-payload'{Verify-Payload}default{Write-Host 'Usage: MODULE.bat install|start|stop|status|verify';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
