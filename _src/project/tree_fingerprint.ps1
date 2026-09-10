param(
    [Parameter(Mandatory = $true)]
    [string]$Root,

    [string]$ExcludeRel = "",

    [Parameter(Mandatory = $true)]
    [string]$Output,

    [string]$ManifestOutput = "",

    [string]$ExpectedManifest = "",

    [string]$ExpectedTreeSha256 = "",

    [string]$ExpectedTreeFileCount = "",

    [string]$DiagnosticOutput = ""
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

function Read-AllBytesLongPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return ,([IO.File]::ReadAllBytes((ConvertTo-ExtendedPath -Path $Path)))
}

function Write-AllBytesLongPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )

    [IO.File]::WriteAllBytes((ConvertTo-ExtendedPath -Path $Path), $Bytes)
}

function Convert-ManifestRowsToMap {
    param([Parameter(Mandatory = $true)][string[]]$Rows)

    $result = New-Object "System.Collections.Generic.Dictionary[string,string]" ([StringComparer]::Ordinal)
    foreach ($row in $Rows) {
        if (-not $row) {
            continue
        }
        $parts = $row.Split([char]0)
        if ($parts.Length -ne 3 -or -not $parts[0] -or -not ($parts[1] -match "^[0-9]+$") -or -not ($parts[2] -match "^[0-9a-f]{64}$")) {
            throw "Invalid tree manifest row"
        }
        if ($result.ContainsKey($parts[0])) {
            throw ("Duplicate tree manifest path: " + $parts[0])
        }
        $result.Add($parts[0], $parts[1] + [char]0 + $parts[2])
    }
    return ,$result
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

if ($ManifestOutput) {
    Write-AllBytesLongPath -Path $ManifestOutput -Bytes $bytes
}

if ($ExpectedManifest -and $DiagnosticOutput) {
    $expectedBytes = Read-AllBytesLongPath -Path $ExpectedManifest
    $expectedSha = [Security.Cryptography.SHA256]::Create()
    try {
        $expectedDigest = $expectedSha.ComputeHash($expectedBytes)
    } finally {
        $expectedSha.Dispose()
    }
    $expectedHex = -join($expectedDigest | ForEach-Object { $_.ToString("x2") })
    $expectedBody = (New-Object Text.UTF8Encoding($false, $true)).GetString($expectedBytes)
    [string[]]$expectedRows = $expectedBody.Split([char]10) | ForEach-Object { $_.TrimEnd([char]13) } | Where-Object { $_ }
    $expected = Convert-ManifestRowsToMap -Rows $expectedRows
    if ($ExpectedTreeSha256 -and $expectedHex -ne $ExpectedTreeSha256) {
        throw "Expected manifest does not match the sealed tree SHA-256"
    }
    if ($ExpectedTreeFileCount -and ([string]$expected.Count) -ne $ExpectedTreeFileCount) {
        throw "Expected manifest does not match the sealed tree file count"
    }
    $actual = Convert-ManifestRowsToMap -Rows $ordered

    $missing = New-Object "System.Collections.Generic.List[string]"
    $added = New-Object "System.Collections.Generic.List[string]"
    $changed = New-Object "System.Collections.Generic.List[string]"
    foreach ($path in $expected.Keys) {
        if (-not $actual.ContainsKey($path)) {
            [void]$missing.Add($path)
        } elseif ($actual[$path] -ne $expected[$path]) {
            [void]$changed.Add($path)
        }
    }
    foreach ($path in $actual.Keys) {
        if (-not $expected.ContainsKey($path)) {
            [void]$added.Add($path)
        }
    }
    [string[]]$missingOrdered = $missing.ToArray()
    [string[]]$addedOrdered = $added.ToArray()
    [string[]]$changedOrdered = $changed.ToArray()
    [Array]::Sort($missingOrdered, [StringComparer]::Ordinal)
    [Array]::Sort($addedOrdered, [StringComparer]::Ordinal)
    [Array]::Sort($changedOrdered, [StringComparer]::Ordinal)

    $diagnostic = New-Object "System.Collections.Generic.List[string]"
    [void]$diagnostic.Add("root=$rootPath")
    [void]$diagnostic.Add("expected_manifest=$ExpectedManifest")
    [void]$diagnostic.Add("expected_file_count=$($expected.Count)")
    [void]$diagnostic.Add("actual_file_count=$($actual.Count)")
    [void]$diagnostic.Add("missing_count=$($missingOrdered.Length)")
    [void]$diagnostic.Add("added_count=$($addedOrdered.Length)")
    [void]$diagnostic.Add("changed_count=$($changedOrdered.Length)")
    foreach ($path in $missingOrdered) {
        [void]$diagnostic.Add("MISSING`t" + $path)
    }
    foreach ($path in $addedOrdered) {
        [void]$diagnostic.Add("ADDED`t" + $path)
    }
    foreach ($path in $changedOrdered) {
        [void]$diagnostic.Add("CHANGED`t" + $path)
    }
    $diagnosticBody = [String]::Join([char]10, $diagnostic.ToArray()) + [char]10
    $diagnosticBytes = (New-Object Text.UTF8Encoding($false)).GetBytes($diagnosticBody)
    Write-AllBytesLongPath -Path $DiagnosticOutput -Bytes $diagnosticBytes
}

@(
    "TREE_SHA256=$hex"
    "TREE_FILE_COUNT=$($ordered.Length)"
) | Set-Content -LiteralPath $Output -Encoding ascii
