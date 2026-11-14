@echo off
cd /d "%~dp0"
python -m pip install --quiet esptool
echo.
echo === 1/2: backup of current firmware ===
python multiboot.py backup
if errorlevel 1 goto end
echo.
echo === 2/2: install multiboot ===
python multiboot.py install
:end
pause
