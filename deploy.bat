@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
py -3 deploy.py
if errorlevel 1 (
  echo.
  echo 部署失败，请检查上方错误信息。
  pause
  exit /b 1
)
echo.
pause
