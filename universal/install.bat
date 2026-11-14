@echo off
cd /d "%~dp0"
python -m pip install --quiet esptool pyserial
python multiboot.py wizard
pause
