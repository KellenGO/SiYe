[CmdletBinding()]
param(
    [string]$Distribution = "dist/SiYe",
    [string]$Output = "dist",
    [string]$PythonPath,
    [string]$IsccPath
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

$pythonPrefix = @()
if (-not $PythonPath) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $python) { throw "Python 3.11+ is required to validate the distribution" }
    $PythonPath = $python.Source
    if ($python.Name -eq "py.exe") { $pythonPrefix = @("-3") }
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python executable not found: $PythonPath"
}

$distributionPath = (Resolve-Path -LiteralPath $Distribution).Path
$outputPath = [IO.Path]::GetFullPath((Join-Path $repoRoot $Output))
[IO.Directory]::CreateDirectory($outputPath) | Out-Null

Invoke-Checked $PythonPath ($pythonPrefix + @(
    "scripts/package_exe.py",
    "--distribution", $distributionPath,
    "--validate-only"
))

$versionFile = Join-Path $distributionPath "RELEASE_VERSION"
$releaseVersion = (Get-Content -LiteralPath $versionFile -Raw).Trim()
if ($releaseVersion -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') {
    throw "Invalid RELEASE_VERSION: $releaseVersion"
}

if (-not $IsccPath) {
    if ($env:INNO_SETUP_COMPILER) {
        $IsccPath = $env:INNO_SETUP_COMPILER
    } else {
        $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($iscc) { $IsccPath = $iscc.Source }
    }
}
if (-not $IsccPath) {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    )
    $IsccPath = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw "Inno Setup compiler was not found. Install Inno Setup 6.3+ or set INNO_SETUP_COMPILER."
}

$installerScript = Join-Path $repoRoot "installer\SiYe.iss"
Invoke-Checked $IsccPath @(
    "/Qp",
    "/DMyAppVersion=$releaseVersion",
    "/DSourceDir=$distributionPath",
    "/DOutputDir=$outputPath",
    $installerScript
)

$installerPath = Join-Path $outputPath "SiYe-Setup-Windows-x64.exe"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Installer output not found: $installerPath"
}
$digest = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$installerPath.sha256"
[IO.File]::WriteAllText(
    $checksumPath,
    "$digest  $([IO.Path]::GetFileName($installerPath))`n",
    [Text.Encoding]::ASCII
)

Write-Host "Installer ready: $installerPath" -ForegroundColor Green
Write-Host "Checksum ready: $checksumPath" -ForegroundColor Green
