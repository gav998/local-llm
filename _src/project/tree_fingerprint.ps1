param(
    [Parameter(Mandatory = $true)]
    [string]$Root,

    [string]$ExcludeRel = "",

    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"

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

    $fileHash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
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
