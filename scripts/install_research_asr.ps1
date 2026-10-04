param([string]$Python = "python")
$ErrorActionPreference = "Stop"
# Optional per-user runtime, separate from the app/EXE and its platform credentials.
$asrData = & $Python -I -c "import os,sys; assert sys.version_info >= (3,11), 'ASR requires Python 3.11+'; print(os.path.abspath(os.path.expanduser(os.environ.get('SIYE_DATA_DIR') or os.path.join(os.environ['LOCALAPPDATA'],'SiYe','data'))))"
if ($LASTEXITCODE -ne 0) { throw "Failed to resolve ASR data directory with Python 3.11+" }
$asrRuntime = Join-Path $asrData "research-asr\runtime"
& $Python -m venv $asrRuntime
if ($LASTEXITCODE -ne 0) { throw "Failed to create optional ASR runtime" }
& (Join-Path $asrRuntime "Scripts\python.exe") -m pip install "faster-whisper==1.2.1"
if ($LASTEXITCODE -ne 0) { throw "Failed to install optional ASR runtime" }
Write-Host "ASR runtime ready. The small model downloads on first transcription."
