#!/bin/sh
cd "$(dirname "$0")"
python3 -m pip install --quiet esptool pyserial 2>/dev/null || pip3 install --quiet esptool pyserial
python3 multiboot.py wizard
