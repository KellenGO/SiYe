@echo off
chcp 65001 >nul
rem Launch SiYe from source (no packaged build required).
rem Source uses port 8090 so it never collides with the packaged app on 8080;
rem override before launch with: set SIYE_PORT=xxxx
setlocal
cd /d "%~dp0"

if not defined SIYE_PORT set "SIYE_PORT=8090"
set "HEALTH_URL=http://127.0.0.1:%SIYE_PORT%/api/health"
set "PAGE_URL=http://127.0.0.1:%SIYE_PORT%"

rem 1. Pick interpreter: local .venv > py -3 > python > installed Python
set "PYEXE="
set "PYFLAGS="
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not defined PYEXE (
    py -3 -c "import sys" >nul 2>&1 && (set "PYEXE=py.exe" & set "PYFLAGS=-3")
)
if not defined PYEXE (
    python -c "import sys" >nul 2>&1 && set "PYEXE=python.exe"
)
if not defined PYEXE (for /d %%P in ("%LOCALAPPDATA%\Programs\Python\Python*") do if exist "%%~fP\python.exe" if not defined PYEXE set "PYEXE=%%~fP\python.exe")
if defined PYEXE goto :PY_FOUND
echo [ERROR] 没找到 Python，请安装 Python 3.11+ 并勾选 "Add to PATH"
echo        下载： https://www.python.org/downloads/
pause
exit /b 1

:PY_FOUND
echo 使用 Python: %PYEXE% %PYFLAGS%
"%PYEXE%" %PYFLAGS% -c "import sys; print('Python 版本:', sys.version.split()[0])"
if errorlevel 1 goto :PY_FAILED

"%PYEXE%" %PYFLAGS% -c "import os; port = int(os.environ['SIYE_PORT']); assert 1 <= port <= 65535, 'SIYE_PORT must be 1..65535'"
if errorlevel 1 goto :PORT_UNAVAILABLE

rem 2. Port already in use? Check whether it is SiYe's own backend.
netstat -ano | findstr /r /c:":%SIYE_PORT% .*LISTENING" >nul 2>&1
if errorlevel 1 goto :CHECK_BIND
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri '%HEALTH_URL%' -TimeoutSec 3; if ($r.status -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if errorlevel 1 goto :PORT_FOREIGN
echo 后端已在运行，直接打开页面：%PAGE_URL%
start "" "%PAGE_URL%"
pause
exit /b 0

:CHECK_BIND
rem Windows can refuse the bind even with no listener, so probe it for real.
"%PYEXE%" %PYFLAGS% -c "import os, socket; s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1); s.bind(('127.0.0.1', int(os.environ['SIYE_PORT']))); s.close()"
if errorlevel 1 goto :PORT_UNAVAILABLE

rem 3. Start the backend (port comes from SIYE_PORT, see base/server_port.py)
echo 正在启动后端，端口 %SIYE_PORT% ...
start "" /b "%PYEXE%" %PYFLAGS% -m api.main

rem 4. Wait for /api/health before opening the browser
set /a TRIES=0
:POLL
set /a TRIES+=1
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri '%HEALTH_URL%' -TimeoutSec 2; if ($r.status -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto :READY
if %TRIES% geq 90 goto :TIMEOUT
ping -n 2 127.0.0.1 >nul
goto :POLL

:READY
echo 已就绪：%PAGE_URL%
start "" "%PAGE_URL%"
echo.
echo 关掉这个窗口就会停止后端。
pause
exit /b 0

:TIMEOUT
echo [ERROR] 后端在 90 轮内没有就绪，请看上面的 Python 报错。
pause
exit /b 1

:PORT_FOREIGN
echo [ERROR] %SIYE_PORT% 端口已被别的程序占用，且不是四野的后端。
echo        让出该端口，或先 set SIYE_PORT=其他端口 再启动。
echo        排查命令： netstat -ano ^| findstr ":%SIYE_PORT%"
pause
exit /b 1

:PORT_UNAVAILABLE
echo [ERROR] 无法监听 127.0.0.1:%SIYE_PORT%，后端尚未启动。
echo        WinError 10013 表示 Windows 拒绝使用该端口；没有监听进程也可能发生。
echo        可在命令提示符中明确换一个端口再启动，例如：
echo        set "SIYE_PORT=18090"
echo        call "%~f0"
echo        浏览器扩展只支持 8080；其他端口请用应用内扫码登录。
pause
exit /b 1

:PY_FAILED
echo [ERROR] Python 无法运行，请检查上面的错误及本目录的 .venv 环境。
pause
exit /b 1
