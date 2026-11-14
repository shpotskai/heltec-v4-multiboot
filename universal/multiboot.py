#!/usr/bin/env python3
"""
Universal multiboot for Heltec WiFi LoRa 32 V4 (ESP32-S3, 16 MB).
Install 2..6 firmwares of your choice and switch between them with the PRG
button (on-device menu) or from the PC. Every slot gets its own NVS and its
own filesystem, so the firmwares never see each other's settings.

  python multiboot.py                      wizard: pick .bin files from my-firmware/
  python multiboot.py plan  slots.txt      show the memory layout, write nothing
  python multiboot.py install slots.txt    erase the board and install
  python multiboot.py list                 show what is installed on the board
  python multiboot.py switch N             start slot N
  python multiboot.py menu                 open the on-device menu
  python multiboot.py update N FILE        new version of the same firmware (settings kept)
  python multiboot.py replace N FILE [NAME]  another firmware in slot N (slot data wiped)
  python multiboot.py wipe N               erase settings/filesystem of slot N
  python multiboot.py names "A" "B" ...    rename menu entries
  python multiboot.py backup [FILE] / restore FILE
  python multiboot.py hash FILE            firmware hash (for RNode: rnodeconf --firmware-hash)

slots.txt, one line per slot:   Name | file.bin | options
  options (optional, comma separated):
    app=3M        app partition size        (default: firmware size + reserve)
    fs=1M         filesystem size           (default 1 MB, 512 KB if space is short)
    fsimg=f.bin   filesystem image to flash (fs size becomes the image size)
    label=spiffs  filesystem partition label (default spiffs)
    fstype=spiffs|littlefs|fat               (partition subtype, default spiffs)

Option: -p PORT (COM5, /dev/ttyACM0 ...). Needs Python 3.8+ and esptool.
"""
import argparse
import binascii
import datetime
import glob
import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SYS = os.path.join(HERE, "system")
FWDIR = os.path.join(HERE, "my-firmware")

FLASH = 0x1000000
OTADATA = 0xE000
MENU = (0x10000, 0x50000)
MBSTATE = (0x60000, 0x10000)
FIRST_SLOT = 0x70000
NVS_SIZE = 0x5000
MAX_SLOTS = 6
FS_SUB = {"spiffs": 0x82, "littlefs": 0x83, "fat": 0x81}
K, M = 1024, 1024 * 1024


def die(msg):
    print("\nERROR: " + msg)
    sys.exit(1)


def up(x, a):
    return (x + a - 1) // a * a


def size_arg(s):
    s = s.strip().upper()
    mul = 1
    if s.endswith("K"):
        mul, s = K, s[:-1]
    elif s.endswith("M"):
        mul, s = M, s[:-1]
    return int(float(s) * mul) if not s.startswith("0X") else int(s, 16)


def human(n):
    return "%.2f MB" % (n / M) if n >= M else "%d KB" % (n // K)


# ---------------------------------------------------------------- images

def image_len(d, off=0):
    """Length of an ESP32 app image starting at off (incl. checksum + sha)."""
    if d[off] != 0xE9:
        raise ValueError("no image magic")
    nseg = d[off + 1]
    pos = off + 24
    for _ in range(nseg):
        _, ln = struct.unpack("<II", d[pos:pos + 8])
        pos += 8 + ln
    pos += 16 - ((pos - off) % 16)
    if d[off + 23]:
        pos += 32
    return pos - off


def load_app(path):
    """Return an app-only image from an app .bin or a merged/factory .bin."""
    d = open(path, "rb").read()
    if len(d) < 0x100 or d[0] != 0xE9:
        die(f"{path}: not an ESP32 firmware image")
    if len(d) > 0x8020 and d[0x8000:0x8002] == b"\xaa\x50":
        # merged image: bootloader + partition table + app. Take the first app.
        app_off = None
        for i in range(0x8000, 0x8C00, 32):
            e = d[i:i + 32]
            if e[:2] != b"\xaa\x50":
                break
            t, st, off, sz = struct.unpack("<BBII", e[2:12])
            if t == 0 and (st == 0x00 or st == 0x10):
                app_off = off
                break
        if app_off is None or app_off >= len(d):
            die(f"{path}: merged image without an app")
        n = image_len(d, app_off)
        print(f"  {os.path.basename(path)}: merged image, app taken from 0x{app_off:x}")
        d = d[app_off:app_off + n]
    else:
        d = d[:image_len(d)]
    if d[12] != 9:
        die(f"{path}: built for chip id {d[12]}, not ESP32-S3")
    pos = image_len(d) - (32 if d[23] else 0)
    if d[23] and hashlib.sha256(d[:pos]).digest() != d[pos:pos + 32]:
        die(f"{path}: image hash mismatch (corrupted file?)")
    return d


# ---------------------------------------------------------------- slots / layout

class Slot:
    def __init__(self, name, path, opts):
        self.name = name.strip()[:19] or os.path.splitext(os.path.basename(path))[0][:19]
        self.path = path
        self.opts = opts
        self.app = load_app(path) if path else None
        self.fsimg = None
        if "fsimg" in opts:
            p = opts["fsimg"]
            if not os.path.isabs(p):
                p = os.path.join(os.path.dirname(path), p)
            self.fsimg = open(p, "rb").read()
        self.label = opts.get("label", "spiffs")[:15]
        self.fstype = opts.get("fstype", "spiffs")
        if self.fstype not in FS_SUB:
            die(f"{self.name}: fstype must be spiffs, littlefs or fat")


def parse_slots_file(path):
    slots = []
    base = os.path.dirname(os.path.abspath(path))
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 2:
            die(f"bad line in {path}: {ln}")
        name, f = parts[0], parts[1]
        if not os.path.isabs(f):
            f = os.path.join(base, f)
        opts = {}
        if len(parts) > 2 and parts[2]:
            for kv in parts[2].split(","):
                k, _, v = kv.partition("=")
                opts[k.strip().lower()] = v.strip()
        slots.append(Slot(name, f, opts))
    if not 1 <= len(slots) <= MAX_SLOTS:
        die(f"need 1..{MAX_SLOTS} slots, got {len(slots)}")
    return slots


def plan(slots, spread=True):
    """Compute offsets. Tries a comfortable layout first, then a compact one.
    Leftover space is shared between the app slots, so a slot can later take
    a bigger or a different firmware (replace)."""
    extra = 0
    for mode in ("normal", "normal", "compact"):
        pos = FIRST_SLOT
        out = []
        for s in slots:
            alen = len(s.app)
            if "app" in s.opts:
                asz = up(size_arg(s.opts["app"]), 0x10000)
            elif mode == "normal":
                asz = up(max(int(alen * 1.25), alen + 0x60000), 0x10000) + extra
            else:
                asz = up(alen + 0x20000, 0x10000)
            if asz < alen:
                die(f"{s.name}: app={human(asz)} is smaller than the firmware ({human(alen)})")
            if s.fsimg is not None:
                fsz = len(s.fsimg)
            elif "fs" in s.opts:
                fsz = up(size_arg(s.opts["fs"]), 0x1000)
            else:
                fsz = (0x100000 if mode == "normal" else 0x80000) - NVS_SIZE
            app_off = up(pos, 0x10000)
            nvs_off = app_off + asz
            fs_off = nvs_off + NVS_SIZE
            pos = fs_off + fsz
            out.append(dict(app_off=app_off, app_size=asz, nvs_off=nvs_off, nvs_size=NVS_SIZE,
                            fs_off=fs_off, fs_size=fsz))
        if pos <= FLASH and mode == "normal" and extra == 0 and spread:
            free_apps = sum(1 for s in slots if "app" not in s.opts)
            if free_apps:
                extra = (FLASH - pos) // free_apps // 0x10000 * 0x10000
                if extra:
                    continue
        if pos <= FLASH:
            for s, l in zip(slots, out):
                s.__dict__.update(l)
            return mode, pos
    die(f"the firmwares do not fit into 16 MB (need {human(pos)}). "
        "Use fewer slots or set smaller fs=/app= sizes.")


def pt_entry(label, t, st, off, size):
    return (b"\xaa\x50" + struct.pack("<BBII", t, st, off, size)
            + label.encode()[:16].ljust(16, b"\0") + struct.pack("<I", 0))


def table_for(slots, k):
    """Partition table in which slot k owns the 'nvs' and filesystem labels."""
    s = slots[k]
    ents = [
        (s.nvs_off, pt_entry("nvs", 1, 0x02, s.nvs_off, s.nvs_size)),
        (OTADATA, pt_entry("otadata", 1, 0x00, OTADATA, 0x2000)),
        (MENU[0], pt_entry("menu", 0, 0x00, MENU[0], MENU[1])),
        (MBSTATE[0], pt_entry("mbstate", 1, 0x40, MBSTATE[0], MBSTATE[1])),
        (s.fs_off, pt_entry(s.label, 1, FS_SUB[s.fstype], s.fs_off, s.fs_size)),
    ]
    for i, x in enumerate(slots):
        ents.append((x.app_off, pt_entry("slot%d" % (i + 1), 0, 0x10 + i, x.app_off, x.app_size)))
    body = b"".join(e for _, e in sorted(ents))
    md5 = b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(body).digest()
    tbl = body + md5
    return tbl + b"\xff" * (0xC00 - len(tbl))


def mbstate_blob(slots):
    hdr = bytearray(b"MBT2" + bytes([1, len(slots), 0, 0]))
    for i in range(MAX_SLOTS):
        if i < len(slots):
            s = slots[i]
            e = s.name.encode("ascii", "replace")[:23].ljust(24, b"\0")
            e += struct.pack("<8I", s.app_off, s.app_size, s.nvs_off, s.nvs_size,
                             s.fs_off, s.fs_size, 0x1000 + i * 0x1000, 0)
        else:
            e = b"\0" * 56
        hdr += e + b"\0" * 8
    hdr += struct.pack("<I", binascii.crc32(bytes(hdr)) & 0xFFFFFFFF)
    blob = bytearray(b"\xff" * MBSTATE[1])
    blob[0:len(hdr)] = hdr
    for i in range(len(slots)):
        t = table_for(slots, i)
        blob[0x1000 + i * 0x1000:0x1000 + i * 0x1000 + len(t)] = t
    return bytes(blob)


def parse_mbstate(blob):
    if blob[:4] != b"MBT2":
        die("the board has no universal multiboot layout (run install first)")
    n = blob[5]
    crc, = struct.unpack("<I", blob[8 + 64 * MAX_SLOTS:12 + 64 * MAX_SLOTS])
    if binascii.crc32(blob[:8 + 64 * MAX_SLOTS]) & 0xFFFFFFFF != crc:
        die("multiboot header CRC error")
    slots = []
    for i in range(n):
        e = blob[8 + 64 * i:8 + 64 * i + 56]
        s = Slot.__new__(Slot)
        s.name = e[:24].split(b"\0")[0].decode("ascii", "replace")
        (s.app_off, s.app_size, s.nvs_off, s.nvs_size, s.fs_off, s.fs_size,
         s.table_off, _) = struct.unpack("<8I", e[24:56])
        t = blob[s.table_off:s.table_off + 0xC00]
        s.label, s.fstype = "spiffs", "spiffs"
        for j in range(0, 0xC00, 32):
            en = t[j:j + 32]
            if en[:2] != b"\xaa\x50":
                break
            ty, st, off, _ = struct.unpack("<BBII", en[2:12])
            if ty == 1 and off == s.fs_off:
                s.label = en[12:28].split(b"\0")[0].decode()
                s.fstype = {v: k for k, v in FS_SUB.items()}.get(st, "spiffs")
        s.opts, s.app, s.fsimg = {}, None, None
        slots.append(s)
    return slots


def print_plan(slots, end):
    print("\n  #  name                 app @ offset         app slot   nvs      filesystem")
    for i, s in enumerate(slots):
        alen = len(s.app) if s.app else 0
        fs = f"{human(s.fs_size)} '{s.label}'" + (" +image" if s.fsimg else "")
        print(f"  {i + 1}  {s.name:20s} 0x{s.app_off:06x}  {human(alen):>9} / {human(s.app_size):>9}"
              f"  {human(s.nvs_size):>6}   {fs}")
    print(f"\n  used {human(end)} of 16 MB, free {human(FLASH - end)}")


# ---------------------------------------------------------------- esptool / serial

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


def esptool(port, *args, after=None):
    new = esptool_major() >= 5
    args = list(args)
    if not new:
        args[0] = args[0].replace("-", "_")
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "-b", "921600"]
    if port:
        cmd += ["-p", port]
    if after:
        cmd += ["--after", after if new else after.replace("-", "_")]
    cmd += args
    print(">", " ".join(cmd))
    if subprocess.run(cmd).returncode != 0:
        die("esptool failed. Check the cable/port. If it keeps failing: hold PRG, tap RST, "
            "release PRG (download mode) and retry.")


def tmp(data):
    fd, p = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return p


def read_flash(port, off, size):
    p = tmp(b"")
    try:
        esptool(port, "read-flash", hex(off), hex(size), p)
        return open(p, "rb").read()
    finally:
        os.remove(p)


def find_port(port):
    if port:
        return port
    try:
        from serial.tools import list_ports
    except ImportError:
        die("pyserial missing: pip install pyserial")
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303A]
    if not ports:
        die("board not found on USB, give the port with -p")
    return ports[0]


def menu_command(port, digit):
    """Open the menu (erase otadata + reset) and send the slot number over USB."""
    esptool(port, "erase-region", hex(OTADATA), "0x2000")
    import serial
    port = find_port(port)
    deadline = time.time() + 25
    buf = b""
    while time.time() < deadline:
        try:
            with serial.Serial(port, 115200, timeout=0.3) as ser:
                while time.time() < deadline:
                    ser.write(digit.encode())
                    buf += ser.read(256)
                    if b"starting slot" in buf or b"is empty" in buf:
                        print(buf.decode(errors="replace").strip().splitlines()[-1])
                        return
                    time.sleep(0.4)
        except Exception:
            time.sleep(0.7)  # port re-enumerating after reset
    die("menu did not answer. Is the firmware in that slot installed? Try: python multiboot.py menu")


# ---------------------------------------------------------------- commands

def system_file(name):
    p = os.path.join(SYS, name)
    if not os.path.exists(p):
        die(f"missing system/{name}")
    return p


def do_install(slots, port, yes):
    mode, end = plan(slots)
    print_plan(slots, end)
    if mode == "compact":
        print("  (compact layout: less spare room for future, bigger firmware versions)")
    if not yes:
        print("\nThis ERASES the whole flash. Make a backup first: python multiboot.py backup")
        if input("Type YES to install: ").strip() != "YES":
            die("cancelled")
    files = [tmp(table_for(slots, 0)), tmp(mbstate_blob(slots))]
    args = ["write-flash",
            "0x0", system_file("bootloader.bin"),
            "0x8000", files[0],
            hex(OTADATA), system_file("ota_data_initial.bin"),
            hex(MENU[0]), system_file("menu.bin"),
            hex(MBSTATE[0]), files[1]]
    for s in slots:
        p = tmp(s.app)
        files.append(p)
        args += [hex(s.app_off), p]
        if s.fsimg:
            p = tmp(s.fsimg)
            files.append(p)
            args += [hex(s.fs_off), p]
    try:
        esptool(port, "erase-flash")
        esptool(port, *args)
    finally:
        for f in files:
            os.remove(f)
    print("\nInstalled. The menu is on the screen: PRG short = next, PRG long = start.")


def wizard(port):
    files = []
    for f in sorted(glob.glob(os.path.join(FWDIR, "*.bin"))):
        with open(f, "rb") as fh:
            head = fh.read(1)
        if head == b"\xe9":
            files.append(f)
        else:
            print(f"  (skipped {os.path.basename(f)}: not a firmware image — a filesystem image "
                  "can be added in slots.txt with fsimg=)")
    print("Universal multiboot for Heltec V4 — setup wizard\n")
    if not files:
        die(f"put your firmware .bin files into the folder:\n  {FWDIR}\nand run again.")
    print(f"Firmware files found in {FWDIR}:")
    for i, f in enumerate(files):
        print(f"  [{i + 1}] {os.path.basename(f)}  ({human(os.path.getsize(f))})")
    print(f"\nWhich files and in what order? Up to {MAX_SLOTS}, e.g.: 1,3,2,4")
    sel = input("> ").replace(" ", "").split(",")
    try:
        idx = [int(x) - 1 for x in sel if x]
        chosen = [files[i] for i in idx]
    except (ValueError, IndexError):
        die("wrong selection")
    if not 1 <= len(chosen) <= MAX_SLOTS:
        die(f"choose 1..{MAX_SLOTS} files")
    lines = []
    for n, f in enumerate(chosen, 1):
        default = os.path.splitext(os.path.basename(f))[0][:19]
        name = input(f"Menu name for slot {n} [{default}]: ").strip() or default
        lines.append(f"{name} | {os.path.relpath(f, HERE)}")
    cfg = os.path.join(HERE, "slots.txt")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write("# Name | file | options (app=, fs=, fsimg=, label=, fstype=)\n")
        fh.write("\n".join(lines) + "\n")
    print(f"\nSaved {cfg} (you can edit it and run: python multiboot.py install slots.txt)")
    slots = parse_slots_file(cfg)
    if input("\nMake a backup of the current flash first? [Y/n]: ").strip().lower() != "n":
        cmd_backup(argparse.Namespace(port=port, file=None))
    do_install(slots, port, yes=False)


def board_slots(port):
    return parse_mbstate(read_flash(port, MBSTATE[0], 0x1000 + 0x1000 * MAX_SLOTS))


def slot_of(slots, n):
    if not 1 <= n <= len(slots):
        die(f"slot must be 1..{len(slots)}")
    return slots[n - 1]


def cmd_backup(a):
    name = a.file or "backup-heltec-v4-%s.bin" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    esptool(a.port, "read-flash", "0", hex(FLASH), name)
    print(f"\nBackup saved: {os.path.abspath(name)}")


def cmd_restore(a):
    if not a.yes and input(f"Overwrite the whole flash with {a.file}? Type YES: ").strip() != "YES":
        die("cancelled")
    esptool(a.port, "write-flash", "0x0", a.file)


def cmd_plan(a):
    slots = parse_slots_file(a.config)
    mode, end = plan(slots)
    print_plan(slots, end)
    print(f"  layout: {mode}")


def cmd_install(a):
    do_install(parse_slots_file(a.config), a.port, a.yes)


def cmd_list(a):
    slots = board_slots(a.port)
    print()
    for i, s in enumerate(slots):
        print(f"  {i + 1}  {s.name:20s} app 0x{s.app_off:06x} ({human(s.app_size)})  "
              f"fs {human(s.fs_size)} '{s.label}'")


def cmd_switch(a):
    n = len(board_slots(a.port))
    if not 1 <= a.slot <= n:
        die(f"slot must be 1..{n}")
    menu_command(a.port, str(a.slot))


def cmd_menu(a):
    esptool(a.port, "erase-region", hex(OTADATA), "0x2000")
    print("\nThe board shows the multiboot menu now.")


def cmd_update(a, wipe=False, name=None):
    slots = board_slots(a.port)
    s = slot_of(slots, a.slot)
    app = load_app(a.file)
    if len(app) > s.app_size:
        die(f"firmware is {human(len(app))}, slot {a.slot} holds {human(s.app_size)}. "
            "Reinstall with a bigger app= size.")
    files = [tmp(app)]
    args = ["write-flash", hex(s.app_off), files[0]]
    if wipe:
        files.append(tmp(b"\xff" * (s.nvs_size + s.fs_size)))
        args += [hex(s.nvs_off), files[-1]]
    if name:
        s.name = name[:19]
        blob = bytearray(read_flash(a.port, MBSTATE[0], MBSTATE[1]))
        new = mbstate_blob_from(slots, blob)
        files.append(tmp(new[:0x1000]))
        args += [hex(MBSTATE[0]), files[-1]]
    try:
        esptool(a.port, *args)
    finally:
        for f in files:
            os.remove(f)
    print(f"\nSlot {a.slot} written" + (" (slot data wiped)" if wipe else " (settings kept)") + ".")


def mbstate_blob_from(slots, old):
    hdr = bytearray(old[:8 + 64 * MAX_SLOTS])
    for i, s in enumerate(slots):
        o = 8 + 64 * i
        hdr[o:o + 24] = s.name.encode("ascii", "replace")[:23].ljust(24, b"\0")
    hdr += struct.pack("<I", binascii.crc32(bytes(hdr)) & 0xFFFFFFFF)
    out = bytearray(old)
    out[0:len(hdr)] = hdr
    return bytes(out)


def cmd_replace(a):
    cmd_update(a, wipe=True, name=a.name or os.path.splitext(os.path.basename(a.file))[0])


def cmd_wipe(a):
    s = slot_of(board_slots(a.port), a.slot)
    esptool(a.port, "erase-region", hex(s.nvs_off), hex(s.nvs_size + s.fs_size))
    print(f"\nSlot {a.slot} settings and filesystem erased.")


def cmd_names(a):
    slots = board_slots(a.port)
    if len(a.names) != len(slots):
        die(f"give {len(slots)} names")
    for s, n in zip(slots, a.names):
        s.name = n[:19]
    old = read_flash(a.port, MBSTATE[0], 0x1000)
    p = tmp(mbstate_blob_from(slots, old + b"\xff" * 0)[:0x1000])
    try:
        esptool(a.port, "write-flash", hex(MBSTATE[0]), p)
    finally:
        os.remove(p)


def cmd_hash(a):
    d = load_app(a.file)
    print(d[-32:].hex() if d[23] else "image has no appended hash")


def main():
    ap = argparse.ArgumentParser(description="Universal multiboot for Heltec V4",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("-p", "--port")
    sp = ap.add_subparsers(dest="cmd")
    s = sp.add_parser("wizard"); s.set_defaults(f=lambda a: wizard(a.port))
    s = sp.add_parser("plan"); s.add_argument("config"); s.set_defaults(f=cmd_plan)
    s = sp.add_parser("install"); s.add_argument("config"); s.add_argument("-y", "--yes", action="store_true"); s.set_defaults(f=cmd_install)
    s = sp.add_parser("list"); s.set_defaults(f=cmd_list)
    s = sp.add_parser("switch"); s.add_argument("slot", type=int); s.set_defaults(f=cmd_switch)
    s = sp.add_parser("menu"); s.set_defaults(f=cmd_menu)
    s = sp.add_parser("update"); s.add_argument("slot", type=int); s.add_argument("file"); s.set_defaults(f=cmd_update)
    s = sp.add_parser("replace"); s.add_argument("slot", type=int); s.add_argument("file"); s.add_argument("name", nargs="?"); s.set_defaults(f=cmd_replace)
    s = sp.add_parser("wipe"); s.add_argument("slot", type=int); s.set_defaults(f=cmd_wipe)
    s = sp.add_parser("names"); s.add_argument("names", nargs="+"); s.set_defaults(f=cmd_names)
    s = sp.add_parser("backup"); s.add_argument("file", nargs="?"); s.set_defaults(f=cmd_backup)
    s = sp.add_parser("restore"); s.add_argument("file"); s.add_argument("-y", "--yes", action="store_true"); s.set_defaults(f=cmd_restore)
    s = sp.add_parser("hash"); s.add_argument("file"); s.set_defaults(f=cmd_hash)
    a = ap.parse_args()
    if not a.cmd:
        wizard(a.port)
    else:
        a.f(a)


if __name__ == "__main__":
    main()
