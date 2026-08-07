[CmdletBinding()]
param(
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:Fancy = [Console]::OutputEncoding.CodePage -eq 65001

function Write-Step {
    param([string]$Label, [string]$Detail = "")
    $tick = if ($script:Fancy) { "  " + [char]0x2714 } else { "  [ok]" }
    Write-Host $tick -ForegroundColor Green -NoNewline
    Write-Host " $Label" -NoNewline
    if ($Detail) { Write-Host "  $Detail" -ForegroundColor DarkGray } else { Write-Host "" }
}

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
        Write-Step "Removed" "sign-in profiles and browser runtime"
    }
    if ($env:DOC_DL_STATE_DIR) {
        Write-Warning "DOC_DL_STATE_DIR is set. Its custom directory was not removed."
    }
}

if ($removedProgram) {
    Write-Step "Removed" $installRoot
    Write-Step "PATH" "entry cleared"
    if (-not $PurgeData) {
        Write-Host ""
        Write-Host "  Kept your sign-in profiles and the browser runtime." -ForegroundColor DarkGray
        Write-Host "  Remove those too: uninstall.ps1 -PurgeData" -ForegroundColor DarkGray
    }
}
else {
    Write-Host "  doc-dl is not installed in $installRoot" -ForegroundColor DarkGray
}
Write-Host ""
