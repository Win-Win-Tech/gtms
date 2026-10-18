@echo off
TITLE MediaMTX - GTMS CCTV Streamer
cd /d "%~dp0"

if not exist mediamtx.exe (
    echo [ERROR] mediamtx.exe not found in this folder!
    echo Please run the PowerShell download command or place mediamtx.exe here.
    pause
    exit /b 1
)

echo Starting MediaMTX with mediamtx.yml...
echo HLS:    http://127.0.0.1:8888
echo WebRTC: http://127.0.0.1:8889
echo API:    http://127.0.0.1:9997
echo.
mediamtx.exe mediamtx.yml
