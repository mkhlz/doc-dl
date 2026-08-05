$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repository = if ($env:DOC_DL_REPOSITORY) { $env:DOC_DL_REPOSITORY } else { "mkhlz/doc-dl" }
$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($architecture -ne "X64") {
    throw "The portable Windows release currently supports x64. Detected: $architecture"
}

$assetName = "doc-dl-windows-x64.zip"
$releaseRoot = "https://github.com/$repository/releases/latest/download"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("doc-dl-install-" + [guid]::NewGuid())
$archivePath = Join-Path $temporaryRoot $assetName
$checksumPath = Join-Path $temporaryRoot "SHA256SUMS"
$installRoot = Join-Path $env:LOCALAPPDATA "Programs\doc-dl"
$backupRoot = $null

New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    Write-Host "Downloading $assetName..."
    Invoke-WebRequest "$releaseRoot/$assetName" -OutFile $archivePath
    Invoke-WebRequest "$releaseRoot/SHA256SUMS" -OutFile $checksumPath

    $escapedName = [regex]::Escape($assetName)
    $checksumLine = Get-Content $checksumPath | Where-Object { $_ -match "\s+$escapedName$" } | Select-Object -First 1
    if (-not $checksumLine) {
        throw "SHA256SUMS does not contain $assetName"
    }
    $expected = ($checksumLine -split "\s+")[0].ToLowerInvariant()
    $actual = (Get-FileHash -Algorithm SHA256 $archivePath).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "Checksum verification failed for $assetName"
    }

    Expand-Archive -LiteralPath $archivePath -DestinationPath $temporaryRoot
    $extracted = Join-Path $temporaryRoot "doc-dl"
    if (-not (Test-Path (Join-Path $extracted "doc-dl.exe"))) {
        throw "The release archive does not contain doc-dl.exe"
    }

    $installParent = Split-Path -Parent $installRoot
    New-Item -ItemType Directory -Force -Path $installParent | Out-Null
    if (Test-Path $installRoot) {
        $backupRoot = "$installRoot.previous-$([guid]::NewGuid())"
        Move-Item -LiteralPath $installRoot -Destination $backupRoot
    }
    Move-Item -LiteralPath $extracted -Destination $installRoot
    if ($backupRoot -and (Test-Path $backupRoot)) {
        Remove-Item -LiteralPath $backupRoot -Recurse -Force
        $backupRoot = $null
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathEntries = @($userPath -split ";" | Where-Object { $_ })
    $alreadyPresent = $pathEntries | Where-Object {
        $_.TrimEnd("\") -ieq $installRoot.TrimEnd("\")
    }
    if (-not $alreadyPresent) {
        $newPath = if ($userPath) { "$installRoot;$userPath" } else { $installRoot }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    }
    if (($env:Path -split ";") -notcontains $installRoot) {
        $env:Path = "$installRoot;$env:Path"
    }

    Write-Host "Installed doc-dl in $installRoot"
    & (Join-Path $installRoot "doc-dl.exe") version
    Write-Host 'Open a new terminal and run: doc-dl "https://example.com/document.pdf"'
}
catch {
    if ($backupRoot -and (Test-Path $backupRoot) -and -not (Test-Path $installRoot)) {
        Move-Item -LiteralPath $backupRoot -Destination $installRoot
    }
    throw
}
finally {
    if (Test-Path $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
