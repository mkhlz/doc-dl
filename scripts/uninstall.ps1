[CmdletBinding()]
param(
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\doc-dl"
$defaultStateRoot = Join-Path $env:LOCALAPPDATA "doc-dl"

function Remove-PathEntry {
    param(
        [Parameter(Mandatory)]
        [string]$Entry,
        [Parameter(Mandatory)]
        [string]$PathValue
    )

    $remaining = @(
        $PathValue -split ";" | Where-Object {
            $_ -and $_.TrimEnd("\\") -ine $Entry.TrimEnd("\\")
        }
    )
    return $remaining -join ";"
}

function Remove-ExpectedDirectory {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$ExpectedPath
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }

    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd("\\")
    $expected = [System.IO.Path]::GetFullPath($ExpectedPath).TrimEnd("\\")
    if (-not $resolved.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an unexpected directory: $resolved"
    }

    Remove-Item -LiteralPath $resolved -Recurse -Force
    return $true
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$updatedUserPath = Remove-PathEntry -Entry $installRoot -PathValue $userPath
if ($updatedUserPath -ne $userPath) {
    [Environment]::SetEnvironmentVariable("Path", $updatedUserPath, "User")
}

$env:Path = Remove-PathEntry -Entry $installRoot -PathValue $env:Path
$removedProgram = Remove-ExpectedDirectory -Path $installRoot -ExpectedPath $installRoot

if ($PurgeData) {
    $removedData = Remove-ExpectedDirectory -Path $defaultStateRoot -ExpectedPath $defaultStateRoot
    if ($removedData) {
        Write-Host "Removed doc-dl state data from $defaultStateRoot"
    }
    if ($env:DOC_DL_STATE_DIR) {
        Write-Warning "DOC_DL_STATE_DIR is set. Its custom directory was not removed."
    }
}

if ($removedProgram) {
    Write-Host "Uninstalled doc-dl from $installRoot"
}
else {
    Write-Host "doc-dl is not installed in $installRoot"
}
Write-Host "Open a new terminal to refresh PATH for future sessions."
