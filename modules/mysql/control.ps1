[CmdletBinding()]
param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1'); Initialize-Module $PSScriptRoot
$Port=3306;$Runtime=Join-Path $PSScriptRoot 'runtime';$MyIni=Join-Path $ConfigRoot 'mysql.ini';$ClientIni=Join-Path $ConfigRoot 'client.ini';$AdminIni=Join-Path $ConfigRoot 'admin.ini';$SecretsPath=Join-Path $StateRoot 'secrets.json'
function Write-Config($Secrets,[string]$InitFile=''){
 $base=$Runtime.Replace('\','/');$data=(Join-Path $DataRoot 'mysql').Replace('\','/');$logs=$LogRoot.Replace('\','/');$init=if($InitFile){"init-file=$($InitFile.Replace('\','/'))`n"}else{''}
 @"
[mysqld]
basedir=$base
datadir=$data
port=$Port
bind-address=127.0.0.1
mysqlx=OFF
skip-name-resolve
skip-log-bin
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
lower_case_table_names=1
max_connections=100
max_allowed_packet=1073741824
innodb_buffer_pool_size=768M
pid-file=$data/mysql.pid
log-error=$logs/mysql-error.log
$init
"@|Set-Content -LiteralPath $MyIni -Encoding ASCII
 @"
[client]
protocol=tcp
host=127.0.0.1
port=$Port
user=ragflow
 password=$($Secrets.ragflow_password)
"@|Set-Content -LiteralPath $ClientIni -Encoding ASCII
 @"
[client]
protocol=tcp
host=127.0.0.1
port=$Port
user=localadmin
password=$($Secrets.root_password)
"@|Set-Content -LiteralPath $AdminIni -Encoding ASCII
}
function Probe-MySql{& (Join-Path $Runtime 'bin\mysql.exe') "--defaults-extra-file=$ClientIni" --batch --skip-column-names -e 'SELECT 1' 2>$null|Out-Null;return $LASTEXITCODE -eq 0}
function Start-MySql{$s=Read-Json $SecretsPath;Write-Config $s;Start-OwnedProcess 'mysql' (Join-Path $Runtime 'bin\mysqld.exe') @("--defaults-file=$MyIni") $Runtime;Wait-Healthy 'mysql' {Probe-MySql}}
function Stop-MySql{if(Get-OwnedProcess 'mysql'){& (Join-Path $Runtime 'bin\mysqladmin.exe') "--defaults-extra-file=$AdminIni" shutdown 2>$null|Out-Null;Start-Sleep 1};if(Get-OwnedProcess 'mysql'){Stop-OwnedProcess 'mysql'}else{Remove-Item (Join-Path $StateRoot 'process-mysql.json') -Force -ErrorAction SilentlyContinue;Write-Host '[OK] mysql stopped'}}
function Install-MySql{
 Begin-Install;Test-Payload;if(Test-Path $SecretsPath){$s=Read-Json $SecretsPath}else{$s=[ordered]@{root_password=New-HexSecret 24;ragflow_password=New-HexSecret 24};Write-JsonAtomic $SecretsPath $s};Write-Config $s
 $db=Join-Path $DataRoot 'mysql';if(-not(Test-Path (Join-Path $db 'mysql') -PathType Container)){
  if(Test-Path $db){Remove-Item $db -Recurse -Force};& (Join-Path $Runtime 'bin\mysqld.exe') "--defaults-file=$MyIni" --initialize-insecure;if($LASTEXITCODE -ne 0){throw 'MySQL initialization failed'}
  $sql=Join-Path $TempRoot 'mysql-init.sql';@"
ALTER USER 'root'@'localhost' IDENTIFIED BY '$($s.root_password)';
CREATE DATABASE IF NOT EXISTS rag_flow CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'ragflow'@'127.0.0.1' IDENTIFIED BY '$($s.ragflow_password)';
ALTER USER 'ragflow'@'127.0.0.1' IDENTIFIED BY '$($s.ragflow_password)';
GRANT ALL PRIVILEGES ON rag_flow.* TO 'ragflow'@'127.0.0.1';
CREATE USER IF NOT EXISTS 'localadmin'@'127.0.0.1' IDENTIFIED BY '$($s.root_password)';
ALTER USER 'localadmin'@'127.0.0.1' IDENTIFIED BY '$($s.root_password)';
GRANT SHUTDOWN ON *.* TO 'localadmin'@'127.0.0.1';
FLUSH PRIVILEGES;
"@|Set-Content $sql -Encoding ASCII;Write-Config $s $sql;Start-OwnedProcess 'mysql' (Join-Path $Runtime 'bin\mysqld.exe') @("--defaults-file=$MyIni") $Runtime;Wait-Healthy 'mysql-bootstrap' {Probe-MySql};Stop-MySql;Remove-Item $sql -Force
 }
 Write-Config $s;Write-Connection ([ordered]@{schema=1;module='mysql';host='127.0.0.1';port=$Port;database='rag_flow';username='ragflow';password=$s.ragflow_password});Set-Installed;Write-Host '[OK] mysql installed'
}
function Verify-Payload{Test-Payload;& (Join-Path $Runtime 'bin\mysqld.exe') --version;if($LASTEXITCODE -ne 0){throw 'mysqld version probe failed'}}
switch($CommandName.ToLowerInvariant()){'install'{Install-MySql}'start'{Assert-Installed;Start-MySql}'stop'{Stop-MySql}'status'{Show-ModuleStatus @('mysql')}'verify'{Test-Payload;if((Get-OwnedProcess 'mysql')-and -not(Probe-MySql)){throw 'MySQL health failed'};Write-Host '[OK] mysql verified'}'verify-payload'{Verify-Payload}default{Write-Host 'Usage: MODULE.bat install|start|stop|status|verify';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
