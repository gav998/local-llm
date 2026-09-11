[CmdletBinding()]param([string]$CommandName='help',[string]$Target='')
. (Join-Path $PSScriptRoot 'lib\runtime.ps1');Initialize-Module $PSScriptRoot
$Port=1200;$Runtime=Join-Path $PSScriptRoot 'runtime';$EsConfig=Join-Path $ConfigRoot 'elasticsearch';$RunDir=Join-Path $LogRoot 'runtime'
function Verify-Payload{Test-Payload;$env:ES_JAVA_HOME=Join-Path $Runtime 'jdk';& (Join-Path $Runtime 'bin\elasticsearch.bat') -V;if($LASTEXITCODE -ne 0){throw 'Elasticsearch version probe failed'}}
function Write-ElasticConfig{New-Item -ItemType Directory -Path $EsConfig,$RunDir -Force|Out-Null;Copy-Item -Path (Join-Path $Runtime 'config\*') -Destination $EsConfig -Recurse -Force;@"
cluster.name: local-llm
node.name: local-llm-node-1
path.data: "$((Join-Path $DataRoot 'index').Replace('\','/'))"
path.logs: "$($LogRoot.Replace('\','/'))"
network.host: 127.0.0.1
http.port: $Port
discovery.type: single-node
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
ingest.geoip.downloader.enabled: false
"@|Set-Content (Join-Path $EsConfig 'elasticsearch.yml') -Encoding UTF8}
function Install-Elastic{Begin-Install;Test-Payload;$env:ES_JAVA_HOME=Join-Path $Runtime 'jdk';& (Join-Path $Runtime 'bin\elasticsearch.bat') -V;if($LASTEXITCODE -ne 0){throw 'Elasticsearch version probe failed'};Write-ElasticConfig;Write-Connection ([ordered]@{schema=1;module='elasticsearch';url="http://127.0.0.1:$Port"});Set-Installed;Write-Host '[OK] elasticsearch installed'}
function Start-Elastic{Write-ElasticConfig;$env:ES_PATH_CONF=$EsConfig;$env:ES_JAVA_HOME=Join-Path $Runtime 'jdk';$env:JAVA_HOME=$env:ES_JAVA_HOME;$env:ES_JAVA_OPTS='-Xms2048m -Xmx2048m -Dlog4j2.disable.jmx=true';Start-OwnedProcess 'elasticsearch' $env:ComSpec @('/d','/c',(Join-Path $Runtime 'bin\elasticsearch.bat')) $RunDir;Wait-Healthy 'elasticsearch' {Test-Http "http://127.0.0.1:$Port/"}}
switch($CommandName.ToLowerInvariant()){'install'{Install-Elastic}'start'{Assert-Installed;Start-Elastic}'stop'{Stop-OwnedProcess 'elasticsearch'}'status'{Show-ModuleStatus @('elasticsearch')}'verify'{Test-Payload;if((Get-OwnedProcess 'elasticsearch')-and -not(Test-Http "http://127.0.0.1:$Port/")){throw 'Elasticsearch health failed'};Write-Host '[OK] elasticsearch verified'}'verify-payload'{Verify-Payload}default{Write-Host 'Usage: MODULE.bat install|start|stop|status|verify';if($CommandName -ne 'help' -and $CommandName){exit 2}}}
