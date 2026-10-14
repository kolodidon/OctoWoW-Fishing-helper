@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Run setup.cmd first. & pause & exit /b 1)
".venv\Scripts\python.exe" cursor_test.py %*
pause
