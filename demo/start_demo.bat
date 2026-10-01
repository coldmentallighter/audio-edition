@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  pause
  exit /b 1
)
python serve.py %*
if errorlevel 1 pause
