#!/usr/bin/env python3
"""
Multiboot for Heltec WiFi LoRa 32 V4 (16 MB): Meshtastic + MeshCore + RNode (Reticulum)
+ MeshCore Repeater.

Commands:
  python multiboot.py backup              full 16 MB flash dump (do this first!)
  python multiboot.py install             erase chip and install everything
  python multiboot.py switch 1|2|3|4      start firmware N
                                          (1=Meshtastic 2=MeshCore 3=RNode 4=MeshCore Repeater)
  python multiboot.py menu                reboot into the on-device menu
  python multiboot.py update N FILE.bin   replace firmware in slot N (settings kept)
  python multiboot.py names "A" "B" "C" "D"  change names shown in the menu
  python multiboot.py rnode               show rnodeconf commands to provision RNode
  python multiboot.py restore FILE.bin    write a backup back to the board

Options:  -p PORT  (e.g. -p COM5 or -p /dev/ttyACM0; auto-detected if omitted)
Needs:    Python 3.8+ and esptool  (pip install esptool)
"""
import argparse
import binascii
import datetime
import hashlib
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.join(HERE, "firmware")

FLASH_SIZE = 0x1000000
OTADATA = (0xE000, 0x2000)
SLOTS = {  # slot: (label, offset, size)
    1: ("Meshtastic", 0x070000, 0x300000),
    2: ("MeshCore",   0x370000, 0x280000),
    3: ("RNode",      0x5F0000, 0x200000),
    4: ("MeshCore Repeater", 0xD50000, 0x200000),
}
MBNAMES = 0xD30000

FILES = [  # offset, file
    (0x000000, "bootloader.bin"),
    (0x008000, "partition-table.bin"),
    (0x00E000, "ota_data_initial.bin"),
    (0x010000, "menu.bin"),
    (0x070000, "meshtastic.bin"),
    (0x370000, "meshcore.bin"),
    (0x5F0000, "rnode.bin"),
    (0x7F0000, "rnode-console-spiffs.bin"),
    (0x9D0000, "meshtastic-littlefs.bin"),
    (0xD30000, "mbnames.bin"),
    (0xD50000, "meshcore-repeater.bin"),
]

# MeshCore companion and repeater both mount the shared SPIFFS and would use the
# same files (/identity/_main.id, /prefs.json). The repeater image is patched to
# use its own names, so it keeps a separate identity and settings.
REPEATER_PATCHES = [(b"/identity\x00", b"/idrepeat\x00"),
                    (b"/prefs.json\x00", b"/rprfs.json\x00")]


def die(msg):
    print("\nERROR: " + msg)
    sys.exit(1)


def esptool_major():
    try:
        out = subprocess.run([sys.executable, "-m", "esptool", "version"],
                             capture_output=True, text=True).stdout
        for tok in out.replace("v", " ").split():
            if tok[:1].isdigit():
                return int(tok.split(".")[0])
    except Exception:
        pass
    die("esptool not found. Install it:  pip install esptool")


def run_esptool(port, *args):
    new = esptool_major() >= 5
    args = list(args)
    if not new:
        args[0] = args[0].replace("-", "_")
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "-b", "921600"]
    if port:
        cmd += ["-p", port]
    cmd += args
    print(">", " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        die("esptool failed. Check the cable/port. If it keeps failing: hold PRG, "
            "press and release RST, release PRG (download mode) and retry.")


def check_image(path, maxsize):
    data = open(path, "rb").read()
    if len(data) < 64 or data[0] != 0xE9:
        die(f"{path} is not an ESP32 app image (no 0xE9 magic). Use the 'update'/app-only .bin, "
            "not a merged/factory image.")
    if data[12:14] != b"\x09\x00":
        die(f"{path} is not built for ESP32-S3.")
    if len(data) > maxsize:
        die(f"{path} is {len(data)} bytes, slot holds only {maxsize}.")
    return data


def fix_image(data):
    """Recompute checksum and appended SHA-256 of an ESP32 app image."""
    d = bytearray(data)
    nseg = d[1]
    pos, chk = 24, 0xEF
    for _ in range(nseg):
        _, ln = struct.unpack("<II", d[pos:pos + 8])
        pos += 8
        for b in d[pos:pos + ln]:
            chk ^= b
        pos += ln
    pos += 15 - (pos % 16)
    d[pos] = chk
    pos += 1
    if d[23]:
        d[pos:pos + 32] = hashlib.sha256(bytes(d[:pos])).digest()
    return bytes(d)


def patch_repeater(data):
    if b"/idrepeat\x00" in data:
        return data  # already patched
    out = data
    for old, new in REPEATER_PATCHES:
        n = out.count(old)
        if n != 1:
            die(f"repeater image: expected one '{old[:-1].decode()}' string, found {n}. "
                "Is this a MeshCore repeater build?")
        out = out.replace(old, new)
    return fix_image(out)


def otadata_for(slot):
    seq = slot  # (seq - 1) % 4 == slot - 1
    entry = struct.pack("<I", seq) + b"\xff" * 20 + struct.pack("<I", 0xFFFFFFFF)
    crc = binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF) & 0xFFFFFFFF
    entry += struct.pack("<I", crc)
    blob = entry + b"\xff" * (OTADATA[1] - len(entry))
    return blob


def tmpfile(data):
    fd, p = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return p


def names_blob(names):
    txt = "".join(n.strip()[:19] + "\n" for n in names).encode("ascii", "replace")
    return txt + b"\xff" * (0x1000 - len(txt))


def cmd_backup(a):
    name = a.file or "backup-heltec-v4-%s.bin" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_esptool(a.port, "read-flash", "0", hex(FLASH_SIZE), name)
    print(f"\nBackup saved: {os.path.abspath(name)}")


def cmd_install(a):
    for off, f in FILES:
        if not os.path.exists(os.path.join(FW, f)):
            die(f"missing firmware/{f}")
    for n, (lbl, off, size) in SLOTS.items():
        fn = dict((o, f) for o, f in FILES)[off]
        check_image(os.path.join(FW, fn), size)
    if not a.yes:
        print("This ERASES the whole flash (all current firmware and settings).")
        print("Make a backup first:  python multiboot.py backup")
        if input("Type YES to continue: ").strip() != "YES":
            die("cancelled")
    run_esptool(a.port, "erase-flash")
    args = ["write-flash"]
    for off, f in FILES:
        args += [hex(off), os.path.join(FW, f)]
    run_esptool(a.port, *args)
    print("\nDone. The board starts the menu: PRG short = next, PRG long = start.")
    print("For RNode run:  python multiboot.py rnode")


def cmd_restore(a):
    if not a.yes and input("Overwrite the whole flash with %s? Type YES: " % a.file).strip() != "YES":
        die("cancelled")
    run_esptool(a.port, "write-flash", "0x0", a.file)


def cmd_switch(a):
    if a.slot not in SLOTS:
        die("slot must be 1, 2, 3 or 4")
    p = tmpfile(otadata_for(a.slot))
    try:
        run_esptool(a.port, "write-flash", hex(OTADATA[0]), p)
    finally:
        os.remove(p)
    print(f"\nStarting {SLOTS[a.slot][0]}.")


def cmd_menu(a):
    run_esptool(a.port, "erase-region", hex(OTADATA[0]), hex(OTADATA[1]))
    print("\nThe board now shows the multiboot menu.")


def cmd_update(a):
    if a.slot not in SLOTS:
        die("slot must be 1, 2, 3 or 4")
    lbl, off, size = SLOTS[a.slot]
    data = check_image(a.file, size)
    path = a.file
    if a.slot == 4:
        path = tmpfile(patch_repeater(data))
        print("repeater image patched for separate identity/settings")
    run_esptool(a.port, "write-flash", hex(off), path)
    print(f"\n{lbl} slot updated, settings kept.")
    if a.slot == 3:
        print("RNode: the firmware hash changed, run:  python multiboot.py rnode --hash-only")


def cmd_names(a):
    if len(a.names) != 4:
        die("give exactly 4 names")
    p = tmpfile(names_blob(a.names))
    try:
        run_esptool(a.port, "write-flash", hex(MBNAMES), p)
    finally:
        os.remove(p)


def cmd_rnode(a):
    path = a.file or os.path.join(FW, "rnode.bin")
    data = check_image(path, SLOTS[3][2])
    h = data[-32:]
    if hashlib.sha256(data[:-32]).digest() != h:
        die("image has no appended SHA-256")
    port = a.port or "PORT"
    print("RNode needs a one-time EEPROM provisioning with rnodeconf (pip install rns).")
    print("1) Start RNode on the board:   python multiboot.py switch 3")
    print("2) Then run:")
    if not a.hash_only:
        print("   rnodeconf --key            (only once per computer: creates a signing key)")
        print(f"   rnodeconf {port} --rom --product c3 --model c8 --hwrev 1")
    print(f"   rnodeconf {port} --firmware-hash {h.hex()}")
    print("3) Check:  rnodeconf %s --info" % port)


def main():
    ap = argparse.ArgumentParser(description="Heltec V4 multiboot tool")
    ap.add_argument("-p", "--port", help="serial port (auto if omitted)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("backup"); s.add_argument("file", nargs="?"); s.set_defaults(f=cmd_backup)
    s = sp.add_parser("install"); s.add_argument("-y", "--yes", action="store_true"); s.set_defaults(f=cmd_install)
    s = sp.add_parser("restore"); s.add_argument("file"); s.add_argument("-y", "--yes", action="store_true"); s.set_defaults(f=cmd_restore)
    s = sp.add_parser("switch"); s.add_argument("slot", type=int); s.set_defaults(f=cmd_switch)
    s = sp.add_parser("menu"); s.set_defaults(f=cmd_menu)
    s = sp.add_parser("update"); s.add_argument("slot", type=int); s.add_argument("file"); s.set_defaults(f=cmd_update)
    s = sp.add_parser("names"); s.add_argument("names", nargs="+"); s.set_defaults(f=cmd_names)
    s = sp.add_parser("rnode"); s.add_argument("file", nargs="?"); s.add_argument("--hash-only", action="store_true"); s.set_defaults(f=cmd_rnode)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
