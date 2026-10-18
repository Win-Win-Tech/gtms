@echo off
TITLE GTMS CCTV Readers
cd /d "%~dp0.."

set "PYTHON_EXE=%~dp0..\..\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

echo =======================================================
echo Starting GTMS CCTV Readers:
echo   [1] Vehicle ANPR Reader  (ANPR_ENABLED=true)
echo   [2] Face CCTV Reader     (FACE_CCTV_ENABLED=true)
echo =======================================================

start "GTMS - Vehicle ANPR Reader" cmd /k "set ANPR_ENABLED=true&& %PYTHON_EXE% manage.py run_anpr_reader"
start "GTMS - Face CCTV Reader" cmd /k "set FACE_CCTV_ENABLED=true&& %PYTHON_EXE% manage.py run_face_reader"

echo Both reader processes have been launched in separate console windows.
echo Close their respective windows to stop them.
pause
