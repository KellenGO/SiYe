# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the node-free Windows executable."""

from pathlib import Path
import shutil

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH).resolve()

if not (ROOT / "webui" / "dist" / "index.html").is_file():
    raise SystemExit("webui/dist/index.html is required; run npm run build first")

node_exe = shutil.which("node")
if not node_exe:
    raise SystemExit("Node.js is required only on the build machine to bundle node.exe")

datas = [
    (str(ROOT / "webui" / "dist"), "webui/dist"),
    (str(ROOT / "webui" / "package.json"), "webui"),
    (str(ROOT / "scripts" / "research_asr_worker.py"), "scripts"),
    (str(ROOT / "scripts" / "install_research_asr.ps1"), "scripts"),
    # 托盘运行时加载的品牌图标；EXE 的 ICO 图标在构建时单独使用。
    (str(ROOT / "assets" / "siye-icon.png"), "assets"),
]
datas.extend((str(path), "libs") for path in (ROOT / "libs").glob("*.js"))
if not (ROOT / "agent_runtime" / "claude.exe").is_file():
    raise SystemExit("AI runtime is required; run python scripts/prepare_agent_runtime.py first")
datas.append((str(ROOT / "agent_runtime"), "agent_runtime"))
binaries = [(node_exe, ".")]
hiddenimports = []

for package in ("playwright", "cv2", "PIL", "xhshow", "pystray", "claude_agent_sdk", "mcp"):
    package_datas, package_binaries, package_hidden = collect_all(package,
        filter_submodules=lambda name: name != "mcp.cli" and not name.startswith("mcp.cli."))
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hidden)

for package in (
    "api",
    "aggregate_search",
    "base",
    "cache",
    "config",
    "constant",
    "media_platform",
    "model",
    "tools",
    "uvicorn",
):
    hiddenimports.extend(collect_submodules(package, filter=lambda name: name not in {
        "tools.file_header_manager", "tools.smoke_cookie_import",
    }))

hiddenimports.extend([
    "desktop_main",
    "tray_main",
    "base.frozen_runtime_smoke",
    "execjs",
    "fastapi",
    "httpx",
    "pydantic",
    "anyio",
    "win32job",
    "win32api",
    "win32con",
    "pywintypes",
])

a = Analysis(
    [str(ROOT / "tray_main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "faster_whisper", "ctranslate2", "av", "onnxruntime",
              "tools.file_header_manager", "tools.smoke_cookie_import"],
    noarchive=False,
)
pyz = PYZ(a.pure)
python_options = [
    ("X utf8", None, "OPTION"),
]

# 两个 EXE 共用同一份入口脚本（tray_main.py），靠参数区分角色：
# - SiYe.exe（console=True）：后端服务 + 平台 worker。
#   worker 子进程经由 sys.executable 拉起并靠 stdin/stdout 管道通信，
#   所以**必须保留控制台**（windowed 模式下 stdout 不可用）。
# - 四野.exe（console=False）：托盘启动器。它把上面那个 EXE 以隐藏窗口方式拉起，
#   自身不跑后端，因此没有控制台也不会影响子进程通信。
exe_backend = EXE(
    pyz,
    a.scripts,
    python_options,
    exclude_binaries=True,
    name="SiYe",
    icon=str(ROOT / "assets" / "siye-icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
exe_launcher = EXE(
    pyz,
    a.scripts,
    python_options,
    exclude_binaries=True,
    name="四野",
    icon=str(ROOT / "assets" / "siye-icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe_backend,
    exe_launcher,
    a.binaries,
    a.datas,
    a.zipfiles,
    strip=False,
    upx=False,
    name="SiYe",
)
