@echo off
rem Starts Astro Coverage Planner with no console window.
rem Every path is built from this file's own folder, so it works from anywhere.
if not exist "%~dp0python\pythonw.exe" (
    echo Extract the whole zip first, then run Start ACP.bat from the extracted folder.
    echo.
    pause
    exit /b 1
)
rem The flags go on the command line because the bundled Python reads a ._pth
rem file, which makes it ignore PYTHONUTF8 and PYTHONDONTWRITEBYTECODE.
start "" "%~dp0python\pythonw.exe" -X utf8 -B "%~dp0app\launcher.py"
