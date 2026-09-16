[CmdletBinding()]
param([string]$ArchiveDirectory='')
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$ManifestPath=Join-Path $PSScriptRoot 'module-archives.json'
$SevenZip=Join-Path $Root 'tools\7za.exe'
$LogRoot=Join-Path $PSScriptRoot 'logs'
$Log=Join-Path $LogRoot 'extract-modules.log'

function Write-Logged([string]$Message,[ConsoleColor]$Color=[ConsoleColor]::Gray){
    Write-Host $Message -ForegroundColor $Color
    Add-Content -LiteralPath $Log -Value $Message -Encoding UTF8
}
function Invoke-LoggedNative([string]$Executable,[object[]]$Arguments,[string]$FailureMessage){
    $PreviousErrorActionPreference=$ErrorActionPreference
    try{
        $ErrorActionPreference='Continue'
        & $Executable @Arguments *>&1|ForEach-Object{$_|Out-Host;Add-Content -LiteralPath $Log -Value ([string]$_) -Encoding UTF8}
        $ExitCode=$LASTEXITCODE
    }finally{$ErrorActionPreference=$PreviousErrorActionPreference}
    if($ExitCode -ne 0){throw "$FailureMessage (exit code $ExitCode; see $Log)"}
}

New-Item -ItemType Directory -Path $LogRoot -Force|Out-Null
Set-Content -LiteralPath $Log -Value ("Extraction started: {0:o}" -f [DateTime]::UtcNow) -Encoding UTF8
try{
    if(Test-Path -LiteralPath (Join-Path $Root 'PREPARE-STACK.bat') -PathType Leaf){
        throw 'Refusing to extract module payloads into the online source tree. Extract the orchestrator ZIP into a separate deployment directory first.'
    }
    if(-not(Test-Path -LiteralPath $SevenZip -PathType Leaf)){throw "Bundled 7-Zip extractor is missing: $SevenZip"}
    if(-not(Test-Path -LiteralPath $ManifestPath -PathType Leaf)){throw "Archive manifest is missing: $ManifestPath"}
    if([string]::IsNullOrWhiteSpace($ArchiveDirectory)){$ArchiveRoot=$Root}
    else{$ArchiveRoot=(Resolve-Path -LiteralPath $ArchiveDirectory -ErrorAction Stop).Path}
    if(-not(Test-Path -LiteralPath $ArchiveRoot -PathType Container)){throw "Archive directory is not a directory: $ArchiveRoot"}
    $Manifest=Get-Content -LiteralPath $ManifestPath -Raw|ConvertFrom-Json
    $Archives=@($Manifest.archives)
    if($Manifest.schema -ne 1 -or $Archives.Count -ne 8){throw 'Archive manifest must describe exactly eight module archives'}

    Write-Logged "[INFO] Archive directory: $ArchiveRoot" Cyan
    $Resolved=@()
    foreach($Entry in $Archives){
        $Archive=Join-Path $ArchiveRoot ([string]$Entry.file)
        if(-not(Test-Path -LiteralPath $Archive -PathType Leaf)){throw "Module archive is missing: $Archive"}
        $Resolved += [pscustomobject]@{name=[string]$Entry.name;path=$Archive}
    }

    foreach($Item in $Resolved){
        Write-Logged "[WAIT] Extracting $($Item.name)" Yellow
        Invoke-LoggedNative $SevenZip @('x','-y','-aoa','-bd',"-o$Root",$Item.path) "$($Item.name) extraction failed"
        $ModuleBat=Join-Path $Root "modules\$($Item.name)\MODULE.bat"
        if(-not(Test-Path -LiteralPath $ModuleBat -PathType Leaf)){throw "Extracted module entry point is missing: $ModuleBat"}
    }
    Write-Logged '[OK] All module archives were extracted; run LOCAL-LLM.bat install' Green
}catch{
    $Message="[ERROR] $($_.Exception.Message)"
    Write-Host $Message -ForegroundColor Red
    Add-Content -LiteralPath $Log -Value $Message -Encoding UTF8
    exit 1
}
