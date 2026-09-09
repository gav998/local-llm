param(
    [Parameter(Mandatory = $true)]
    [string]$Root,

    [string]$ExcludeRel = "",

    [Parameter(Mandatory = $true)]
    [string]$Output
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

function Get-FileSha256LongPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [IO.File]::Open(
        (ConvertTo-ExtendedPath -Path $Path),
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash($stream)
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
    return -join($digest | ForEach-Object { $_.ToString("x2") })
}

$rootItem = Get-Item -LiteralPath $Root -Force
if (-not $rootItem.PSIsContainer) {
    throw "Tree root is not a directory"
}
if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Tree root is a reparse point"
}

$rootPath = $rootItem.FullName.TrimEnd([char[]]"\/")
$excludes = @(
    [string]$ExcludeRel -split ";" |
        ForEach-Object { $_.Replace("\", "/").TrimStart("/") } |
        Where-Object { $_ }
)

$rows = New-Object "System.Collections.Generic.List[string]"
foreach ($item in Get-ChildItem -LiteralPath $rootPath -Force -Recurse -ErrorAction Stop) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw ("Reparse point in sealed tree: " + $item.FullName)
    }
    if ($item.PSIsContainer) {
        continue
    }

    $rel = $item.FullName.Substring($rootPath.Length).TrimStart([char[]]"\/").Replace("\", "/")
    $skip = $false
    foreach ($exclude in $excludes) {
        if ($exclude.EndsWith("/")) {
            if ($rel.StartsWith($exclude, [StringComparison]::OrdinalIgnoreCase)) {
                $skip = $true
                break
            }
        } elseif ([StringComparer]::OrdinalIgnoreCase.Equals($rel, $exclude)) {
            $skip = $true
            break
        }
    }
    if ($skip) {
        continue
    }

    # The Windows PowerShell 5.1 hash cmdlet resolves paths through its legacy
    # provider and fails beyond MAX_PATH. 7-Zip can create such dependency
    # paths, so hash through a FileStream with the Win32 extended-path prefix.
    $fileHash = Get-FileSha256LongPath -Path $item.FullName
    [void]$rows.Add($rel + [char]0 + $item.Length + [char]0 + $fileHash)
}

[string[]]$ordered = $rows.ToArray()
[Array]::Sort($ordered, [StringComparer]::Ordinal)
$body = [String]::Join([char]10, $ordered)
if ($ordered.Length -gt 0) {
    $body += [char]10
}

$bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($body)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $digest = $sha.ComputeHash($bytes)
} finally {
    $sha.Dispose()
}
$hex = -join($digest | ForEach-Object { $_.ToString("x2") })

@(
    "TREE_SHA256=$hex"
    "TREE_FILE_COUNT=$($ordered.Length)"
) | Set-Content -LiteralPath $Output -Encoding ascii
