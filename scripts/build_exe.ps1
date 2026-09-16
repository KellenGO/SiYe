[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$PythonPath
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
$pythonCommand = $null
if ($PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "The specified Python build environment was not found: $PythonPath"
    }
    $pythonCommand = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        $python = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $python) { throw "Python 3.11+ build environment was not found" }
    $pythonCommand = $python.Source
    if ($python.Name -eq "py.exe") { $pythonPrefix = @("-3") }
}

if (-not $SkipTests) {
    Push-Location (Join-Path $repoRoot "webui")
    try {
        Invoke-Checked npm @("ci")
        Invoke-Checked npm @("run", "test:search")
        Invoke-Checked npm @("run", "build")
    } finally {
        Pop-Location
    }

    Invoke-Checked $pythonCommand ($pythonPrefix + @("-m", "pytest", "-q"))
}

Invoke-Checked $pythonCommand ($pythonPrefix + @("-m", "PyInstaller", "--clean", "--noconfirm", "MediaCrawler.spec"))

$distribution = Join-Path $repoRoot "dist\SiYe"
$extensionTarget = Join-Path $distribution "browser_extension"
if (Test-Path $extensionTarget) {
    Remove-Item -LiteralPath $extensionTarget -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $repoRoot "browser_extension") -Destination $extensionTarget -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination (Join-Path $distribution "LICENSE") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination (Join-Path $distribution "README.md") -Force

$baseVersion = & $pythonCommand @pythonPrefix -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) { throw "Unable to read the version from pyproject.toml" }
$releaseVersion = $baseVersion.Trim()
if ($env:GITHUB_REF_TYPE -eq "tag" -and $env:GITHUB_REF_NAME) {
    $tagVersion = $env:GITHUB_REF_NAME -replace '^v', ''
    if ($tagVersion -ne $releaseVersion) {
        throw "Git tag $($env:GITHUB_REF_NAME) does not match pyproject.toml version $releaseVersion"
    }
}
$versionFile = Join-Path $distribution "RELEASE_VERSION"
Set-Content -LiteralPath $versionFile -Value $releaseVersion -Encoding ascii

Invoke-Checked $pythonCommand ($pythonPrefix + @("scripts/package_exe.py", "--distribution", "dist/SiYe", "--output", "dist"))
if (-not $SkipInstaller) {
    Invoke-Checked powershell.exe @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "scripts/build_installer.ps1",
        "-Distribution", "dist/SiYe",
        "-Output", "dist",
        "-PythonPath", $pythonCommand
    )
}
Write-Host "EXE distribution ready: dist/SiYe/SiYe.exe" -ForegroundColor Green
