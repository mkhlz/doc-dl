$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repository = if ($env:DOC_DL_REPOSITORY) { $env:DOC_DL_REPOSITORY } else { "mkhlz/doc-dl" }
$architecture = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
}
else {
    $env:PROCESSOR_ARCHITECTURE
}
if ($architecture -notin @("AMD64", "ARM64")) {
    throw "The portable Windows release currently supports x64. Detected: $architecture"
}

$variant = if ($env:DOC_DL_VARIANT) { $env:DOC_DL_VARIANT.ToLowerInvariant() } else { "slim" }
if ($variant -notin @("slim", "full")) {
    throw "DOC_DL_VARIANT must be 'slim' or 'full'. Got: $variant"
}
$assetName = if ($variant -eq "full") { "doc-dl_win_full.zip" } else { "doc-dl_win.zip" }
$releaseRoot = "https://github.com/$repository/releases/latest/download"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("doc-dl-install-" + [guid]::NewGuid())
$archivePath = Join-Path $temporaryRoot $assetName
$checksumPath = Join-Path $temporaryRoot "SHA2-256SUMS"
$installRoot = Join-Path $env:LOCALAPPDATA "Programs\doc-dl"
$backupRoot = $null
# Keep in sync with RELEASE_NAME in src/doc_dl/cli.py.
$releaseName = "Alexandria"

# A returning user has already met the tool, so the wordmark is shown only the
# first time. Everything decorative degrades on a console that cannot draw it.
$script:WasInstalled = Test-Path (Join-Path $installRoot "doc-dl.exe")
$script:PreviousVersion = $null
if ($script:WasInstalled) {
    try {
        $script:PreviousVersion = (& (Join-Path $installRoot "doc-dl.exe") version --quiet) -replace '^doc-dl\s+', ''
    }
    catch { $script:PreviousVersion = $null }
}
# [Console]::OutputEncoding reflects the process's code page, not whether the
# terminal itself can draw the glyphs -- Windows Terminal renders full
# Unicode regardless of that setting, while the classic conhost window never
# will no matter what it's set to. WT_SESSION/ConEmuANSI/TERM_PROGRAM are the
# actual signal (same check used by the installed binary itself).
$script:Fancy = [bool]$env:WT_SESSION -or ($env:ConEmuANSI -eq "ON") -or [bool]$env:TERM_PROGRAM

function Write-Step {
    param([string]$Label, [string]$Detail = "")
    $tick = if ($script:Fancy) { "  " + [char]0x2714 } else { "  [ok]" }
    Write-Host $tick -ForegroundColor Green -NoNewline
    Write-Host " $Label" -NoNewline
    if ($Detail) { Write-Host "  $Detail" -ForegroundColor DarkGray } else { Write-Host "" }
}

function Write-Banner {
    # The wordmark, split so "doc-" reads white and "dl" reads brand blue.
    Write-Host ""
    if ($script:Fancy) {
        $b = [char]0x2588; $tl = [char]0x2554; $tr = [char]0x2557
        $bl = [char]0x255A; $br = [char]0x255D; $h = [char]0x2550; $v = [char]0x2551
        $lines = @(
            @("$b$b$b$b$b$b$tr  $b$b$b$b$b$b$tr  $b$b$b$b$b$b$tr      ", "$b$b$b$b$b$b$tr  $b$b$tr"),
            @("$b$b$tl$h$h$b$b$tr$b$b$tl$h$h$h$b$b$tr$b$b$tl$h$h$h$h$br      ", "$b$b$tl$h$h$b$b$tr $b$b$v"),
            @("$b$b$v  $b$b$v$b$b$v   $b$b$v$b$b$v     $b$b$b$b$b$tr", "$b$b$v  $b$b$v $b$b$v"),
            @("$b$b$v  $b$b$v$b$b$v   $b$b$v$b$b$v     $bl$h$h$h$h$br", "$b$b$v  $b$b$v $b$b$v"),
            @("$b$b$b$b$b$b$tl$br$bl$b$b$b$b$b$b$tl$br$bl$b$b$b$b$b$b$tr      ", "$b$b$b$b$b$b$tl$br $b$b$b$b$b$b$b$tr"),
            @("$bl$h$h$h$h$h$br  $bl$h$h$h$h$h$br  $bl$h$h$h$h$h$br      ", "$bl$h$h$h$h$h$br  $bl$h$h$h$h$h$h$br")
        )
        foreach ($pair in $lines) {
            Write-Host "   $($pair[0])" -ForegroundColor White -NoNewline
            Write-Host $pair[1] -ForegroundColor Blue
        }
    }
    else {
        Write-Host "   ######    #####    #####            ######   ##"       -ForegroundColor White
        Write-Host "   ##   ##  ##   ##  ##   ##           ##   ##  ##"       -ForegroundColor White
        Write-Host "   ##   ##  ##   ##  ##        #####   ##   ##  ##"       -ForegroundColor White
        Write-Host "   ##   ##  ##   ##  ##                ##   ##  ##"       -ForegroundColor White
        Write-Host "   ##   ##  ##   ##  ##   ##           ##   ##  ##"       -ForegroundColor White
        Write-Host "   ######    #####    #####            ######   #######"  -ForegroundColor White
    }
    Write-Host ""
    Write-Host "        a resilient command-line document downloader" -ForegroundColor DarkGray
    Write-Host ""
}

New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    Write-Host ""
    Write-Host "  Fetching $assetName" -ForegroundColor Cyan
    Invoke-WebRequest "$releaseRoot/$assetName" -OutFile $archivePath
    Invoke-WebRequest "$releaseRoot/SHA2-256SUMS" -OutFile $checksumPath

    $escapedName = [regex]::Escape($assetName)
    $checksumLine = Get-Content $checksumPath | Where-Object { $_ -match "\s+$escapedName$" } | Select-Object -First 1
    if (-not $checksumLine) {
        throw "SHA2-256SUMS does not contain $assetName"
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

    $newVersion = (& (Join-Path $installRoot "doc-dl.exe") version --quiet) -replace '^doc-dl\s+', ''

    if ($script:WasInstalled) {
        if ($script:PreviousVersion -and $script:PreviousVersion -ne $newVersion) {
            Write-Step "Updated doc-dl" "$($script:PreviousVersion) -> $newVersion `"$releaseName`""
        }
        else {
            Write-Step "Reinstalled doc-dl" "$newVersion `"$releaseName`""
        }
        Write-Host ""
    }
    else {
        Write-Banner
        Write-Step "Verified" "SHA-256 matches the published checksum"
        Write-Step "Installed" $installRoot
        Write-Step "On PATH" "open a new terminal to pick it up"
        Write-Host ""
        Write-Host "  doc-dl $newVersion `"$releaseName`" is ready." -ForegroundColor Green
        Write-Host ""
        Write-Host "  Try it" -ForegroundColor DarkGray -NoNewline
        Write-Host "  doc-dl " -NoNewline
        Write-Host '"https://example.com/report.pdf"' -ForegroundColor Cyan
        Write-Host ""
    }
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
