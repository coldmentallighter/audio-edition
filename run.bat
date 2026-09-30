@echo off
chcp 65001 >nul 2>nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo  ============================================
echo   AudioEdition - Local Audio Toolbox
echo  ============================================
echo.

where python >nul 2>nul
if errorlevel 1 goto NOPYTHON
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo  [1/4] Python %PYVER%

set "MISSING="
for %%t in (ffmpeg ffprobe flac metaflac) do call :CHECKTOOL %%t
if defined MISSING (
  echo.
  echo  [notice] Missing tools: the app can still start, but related features will fail.
  echo         install: winget install Gyan.FFmpeg Xiph.FLAC
  echo.
)

python -c "import fastapi,uvicorn,mutagen,multipart" >nul 2>nul
if errorlevel 1 goto INSTALL
echo  [3/4] Dependencies satisfied
goto RUN

:INSTALL
echo  [3/4] Dependencies missing, installing...
python -m pip install --disable-pip-version-check fastapi "uvicorn[standard]" mutagen python-multipart
if errorlevel 1 goto PIPFAIL
echo  [3/4] Dependencies installed

:RUN
if not defined AE_PORT set "AE_PORT=8765"
if not defined AE_HOST set "AE_HOST=127.0.0.1"

rem ---- Check the port first: don't wait for uvicorn to print its banner and then report 10048,
rem ---- by that time the browser may already be open ----
call :PORTPID %AE_PORT%
if defined BUSYPID goto PORTBUSY

echo  [4/4] starting  http://%AE_HOST%:%AE_PORT%
echo.
echo  stop with Ctrl+C or close this window
echo.

rem Poll the health endpoint; only open the browser once the service really answers
rem (skip on failure so we don't point at someone else's service)
if not defined AE_NO_BROWSER start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 40;$i++){try{if((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 'http://%AE_HOST%:%AE_PORT%/api/health').StatusCode -eq 200){Start-Process 'http://%AE_HOST%:%AE_PORT%';break}}catch{};Start-Sleep -Milliseconds 400}" >nul 2>nul

python -m backend.app
set "EXITCODE=%errorlevel%"
echo.
if "%EXITCODE%"=="0" goto STOPPED
if "%EXITCODE%"=="-1073741510" goto STOPPED
if "%EXITCODE%"=="3221225786" goto STOPPED
goto CRASH

:STOPPED
echo  service stopped
goto END

:CHECKTOOL
where %1 >nul 2>nul
if errorlevel 1 (
  echo  [warning] missing %1
  set "MISSING=1"
) else (
  echo  [2/4] %1 found
)
goto :eof

rem Echo the PID listening on the given port; BUSYPID stays empty if none
:PORTPID
set "BUSYPID="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %1 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"`) do set "BUSYPID=%%p"
goto :eof

:PORTBUSY
set "BUSYNAME=unknown process"
for /f "usebackq delims=" %%n in (`powershell -NoProfile -Command "$p=Get-Process -Id %BUSYPID% -ErrorAction SilentlyContinue; if($p){$p.ProcessName}"`) do set "BUSYNAME=%%n"
echo  [error] Port %AE_PORT% is already in use, the service cannot start
echo         Occupied by: %BUSYNAME% (PID %BUSYPID%)
echo.
echo  Two ways to fix it, pick either one:
echo.
echo    A. Start on a different port
echo         set AE_PORT=9000
echo         run.bat
echo.
echo    B. Kill the process holding the port
echo         taskkill /PID %BUSYPID% /F
echo.
echo  Tip: if that is a leftover service window from last time, just close it.
set "BUSYPID="
goto PAUSEEND

:NOPYTHON
echo  [error] python not found
echo         Please install Python 3.10+ and check "Add to PATH"
echo         Download: https://www.python.org/downloads/
goto PAUSEEND

:PIPFAIL
echo  [error] Failed to install dependencies. Check your network and try again.
goto PAUSEEND

:CRASH
echo  [error] service crashed with exit code %EXITCODE%
echo         If this is 10048 / WinError 10048, port %AE_PORT% is in use.
echo         Run  set AE_PORT=9000  then run.bat again
goto PAUSEEND

:PAUSEEND
pause

:END
endlocal
