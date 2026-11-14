#!/bin/sh
cd "$(dirname "$0")"
python3 -m pip install --quiet esptool 2>/dev/null || pip3 install --quiet esptool
echo "=== 1/2: backup of current firmware ==="
python3 multiboot.py backup || exit 1
echo "=== 2/2: install multiboot ==="
python3 multiboot.py install
