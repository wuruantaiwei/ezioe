@echo off
rem Chay bo test that sau khi IOE mo lai Tu luyen (20:00).
cd /d "%~dp0.."
echo ==== %DATE% %TIME% ==== >> data\test_run.log
python run.py run --turbo --jobs 2 --with-speaking >> data\test_run.log 2>&1
echo exit=%ERRORLEVEL% >> data\test_run.log
