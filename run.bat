@echo off
chcp 936 >nul 2>nul
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo  ============================================
echo   AudioEdition - 本地音频工具箱
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
  echo  [提示] 缺少工具仍可启动，但相关功能会报错。
  echo         安装: winget install Gyan.FFmpeg Xiph.FLAC
  echo.
)

python -c "import fastapi,uvicorn,mutagen,multipart" >nul 2>nul
if errorlevel 1 goto INSTALL
echo  [3/4] 依赖已满足
goto RUN

:INSTALL
echo  [3/4] 缺少依赖，正在安装...
python -m pip install --disable-pip-version-check fastapi "uvicorn[standard]" mutagen python-multipart
if errorlevel 1 goto PIPFAIL
echo  [3/4] 依赖安装完成

:RUN
if not defined AE_PORT set "AE_PORT=8765"
if not defined AE_HOST set "AE_HOST=127.0.0.1"

rem ---- 先查端口：别等 uvicorn 打完启动横幅才报 10048，那时浏览器可能已经开了 ----
call :PORTPID %AE_PORT%
if defined BUSYPID goto PORTBUSY

echo  [4/4] 启动服务  http://%AE_HOST%:%AE_PORT%
echo.
echo  按 Ctrl+C 停止服务
echo.

rem 轮询健康检查，服务真的答上了才开浏览器（失败就不开，免得指向别人的服务）
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 40;$i++){try{if((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 'http://%AE_HOST%:%AE_PORT%/api/health').StatusCode -eq 200){Start-Process 'http://%AE_HOST%:%AE_PORT%';break}}catch{};Start-Sleep -Milliseconds 400}" >nul 2>nul

python -m backend.app
set "EXITCODE=%errorlevel%"
echo.
if "%EXITCODE%"=="0" goto STOPPED
if "%EXITCODE%"=="-1073741510" goto STOPPED
if "%EXITCODE%"=="3221225786" goto STOPPED
goto CRASH

:STOPPED
echo  服务已停止
goto END

:CHECKTOOL
where %1 >nul 2>nul
if errorlevel 1 (
  echo  [警告] 未找到 %1
  set "MISSING=1"
) else (
  echo  [2/4] %1 已就绪
)
goto :eof

rem 回显监听该端口的 PID；没有则 BUSYPID 为空
:PORTPID
set "BUSYPID="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %1 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"`) do set "BUSYPID=%%p"
goto :eof

:PORTBUSY
set "BUSYNAME=未知进程"
for /f "usebackq delims=" %%n in (`powershell -NoProfile -Command "$p=Get-Process -Id %BUSYPID% -ErrorAction SilentlyContinue; if($p){$p.ProcessName}"`) do set "BUSYNAME=%%n"
echo  [错误] 端口 %AE_PORT% 已被占用，服务无法启动
echo         占用者：%BUSYNAME% (PID %BUSYPID%)
echo.
echo  两种解决办法，任选其一：
echo.
echo    A. 换个端口启动
echo         set AE_PORT=9000
echo         run.bat
echo.
echo    B. 结束占用端口的进程
echo         taskkill /PID %BUSYPID% /F
echo.
echo  提示：如果那是上一次没关干净的服务窗口，直接关掉它即可。
set "BUSYPID="
goto PAUSEEND

:NOPYTHON
echo  [错误] 未找到 python
echo         请安装 Python 3.10+ 并勾选 Add to PATH
echo         下载: https://www.python.org/downloads/
goto PAUSEEND

:PIPFAIL
echo  [错误] 依赖安装失败，请检查网络后重试
goto PAUSEEND

:CRASH
echo  [错误] 服务异常退出，代码 %EXITCODE%
echo         若为 10048 / WinError 10048，说明端口 %AE_PORT% 被占用。
echo         先执行  set AE_PORT=9000  再重新运行 run.bat
goto PAUSEEND

:PAUSEEND
pause

:END
endlocal
