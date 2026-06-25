@echo off
REM PC 서버 더블클릭 런처 — run_server.ps1 을 실행합니다.
REM 옵션을 주려면 PowerShell 에서 직접 .\run_server.ps1 -Demo 처럼 실행하세요.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_server.ps1" %*
pause
