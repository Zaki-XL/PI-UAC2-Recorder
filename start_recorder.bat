@echo off
title POCO M7 Lossless Audio Recorder
cd /d "%~dp0"

echo ==================================================
echo   POCO M7 Lossless Audio Recorder (PC Edition)
echo ==================================================
echo.
echo Opening web recorder at http://localhost:8088 ...
echo Press Ctrl+C in this window to stop the server.
echo.

python src\recorder_win.py 8088
pause
