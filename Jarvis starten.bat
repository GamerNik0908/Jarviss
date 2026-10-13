@echo off
chcp 65001 >nul
title J.A.R.V.I.S.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 start.py %*
) else (
    python start.py %*
)

if errorlevel 1 (
    echo.
    echo Jarvis wurde mit einem Fehler beendet. Ist Python 3.11+ installiert? https://www.python.org/downloads/
)
pause
