@echo off
rem Build .exe: double-click hoặc chạy trong cmd. Cần: pip install pyinstaller
cd /d "%~dp0"
python packaging\build.py %*
pause
