param([string]$Python = "python")
$ErrorActionPreference = "Stop"
# Optional per-user runtime, separate from the app/EXE and its platform credentials.
$asrData = if ($env:SIYE_DATA_DIR) { $env:SIYE_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "SiYe\data" }
$asrRuntime = Join-Path $asrData "research-asr\runtime"
& $Python -m venv $asrRuntime
if ($LASTEXITCODE -ne 0) { throw "Failed to create optional ASR runtime" }
& (Join-Path $asrRuntime "Scripts\python.exe") -m pip install "faster-whisper==1.2.1"
if ($LASTEXITCODE -ne 0) { throw "Failed to install optional ASR runtime" }
Write-Host "ASR runtime ready. The small model downloads on first transcription."
