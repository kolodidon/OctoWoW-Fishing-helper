@echo off
cd /d "%~dp0"
py -3.11 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Ready. Run start.cmd
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.11 x64 and retry.
pause
exit /b 1
