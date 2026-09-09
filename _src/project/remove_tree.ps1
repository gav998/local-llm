param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = "Stop"

function ConvertTo-ExtendedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith("\\?\", [StringComparison]::Ordinal)) {
        return $Path
    }
    if ($Path.StartsWith("\\", [StringComparison]::Ordinal)) {
        return "\\?\UNC\" + $Path.Substring(2)
    }
    return "\\?\" + [IO.Path]::GetFullPath($Path)
}

function Remove-EntryLongPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $attributes = [IO.File]::GetAttributes($Path)
    $isDirectory = ($attributes -band [IO.FileAttributes]::Directory) -ne 0
    $isReparsePoint = ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0

    if ($isReparsePoint) {
        throw "Refusing to remove a reparse point"
    }

    if ($isDirectory) {
        foreach ($child in [IO.Directory]::EnumerateFileSystemEntries($Path)) {
            Remove-EntryLongPath -Path $child
        }
        if (($attributes -band [IO.FileAttributes]::ReadOnly) -ne 0) {
            [IO.File]::SetAttributes(
                $Path,
                ($attributes -bxor [IO.FileAttributes]::ReadOnly)
            )
        }
        [IO.Directory]::Delete($Path, $false)
        return
    }

    [IO.File]::SetAttributes($Path, [IO.FileAttributes]::Normal)
    [IO.File]::Delete($Path)
}

$rootItem = Get-Item -LiteralPath $Root -Force
if (-not $rootItem.PSIsContainer) {
    throw "Removal root is not a directory"
}
if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Removal root is a reparse point"
}

$rootPath = $rootItem.FullName.TrimEnd([char[]]"\/")
$volumeRoot = [IO.Path]::GetPathRoot($rootPath).TrimEnd([char[]]"\/")
if ([StringComparer]::OrdinalIgnoreCase.Equals($rootPath, $volumeRoot)) {
    throw "Refusing to remove a volume root"
}

Remove-EntryLongPath -Path (ConvertTo-ExtendedPath -Path $rootPath)
