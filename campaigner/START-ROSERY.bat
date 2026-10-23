@echo off
rem ROSERY: starts the system and keeps it up to date. First time: add your flags once (see run_owner.py).
cd /d "%~dp0"
python -B run_owner.py %*
pause
