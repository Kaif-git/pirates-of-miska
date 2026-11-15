@echo off
cd /d "%~dp0"
python play.py %*
if errorlevel 1 pause
