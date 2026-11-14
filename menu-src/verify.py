"""Static checks of the multiboot package: layout, images, otadata logic."""
import binascii, hashlib, os, struct, sys
sys.path.insert(0, sys.argv[1])
import multiboot as m

fw = os.path.join(sys.argv[1], "firmware")
flash = bytearray(b"\xff" * m.FLASH_SIZE)
ok = True

def fail(msg):
    global ok; ok = False; print("FAIL:", msg)

# partition table
pt = open(os.path.join(fw, "partition-table.bin"), "rb").read()
parts = []
for i in range(0, len(pt), 32):
    e = pt[i:i+32]
    if e[:2] != b"\xaa\x50": break
    t, st, off, sz = struct.unpack("<BBII", e[2:12])
    parts.append((e[12:28].rstrip(b"\0").decode(), t, st, off, sz))
print("partitions:")
for p in parts: print("  %-11s type=%d sub=0x%02x off=0x%06x size=0x%06x" % p)

# overlaps
spans = sorted((p[3], p[3]+p[4], p[0]) for p in parts)
for a, b in zip(spans, spans[1:]):
    if a[1] > b[0]: fail(f"overlap {a[2]} / {b[2]}")
if spans[-1][1] > m.FLASH_SIZE: fail("table exceeds 16MB")

def part_at(off):
    for p in parts:
        if p[3] <= off < p[3]+p[4]: return p
    return None

# files placed inside partitions and not overlapping
used = []
for off, f in m.FILES:
    d = open(os.path.join(fw, f), "rb").read()
    end = off + len(d)
    for (a, b, n) in used:
        if off < b and a < end: fail(f"{f} overlaps {n}")
    used.append((off, end, f))
    flash[off:end] = d
    p = part_at(off)
    if off >= 0x10000 or f == "ota_data_initial.bin":
        if p is None: fail(f"{f} not in a partition"); continue
        if p[3] != off: fail(f"{f} not at partition start ({p[0]})")
        if len(d) > p[4]: fail(f"{f} larger than {p[0]}")
        exact = f in ("rnode-console-spiffs.bin", "meshtastic-littlefs.bin")
        if exact and len(d) != p[4]: fail(f"{f} size {len(d):#x} != partition {p[4]:#x}")
        print(f"  {f:28s} -> {p[0]:11s} {len(d):>8} / {p[4]:>8} bytes")

# app images: checksum + sha256
def verify_app(d, name):
    hdr = d[:24]
    nseg = hdr[1]; hash_app = hdr[23]
    pos = 24; chk = 0xEF
    for _ in range(nseg):
        addr, ln = struct.unpack("<II", d[pos:pos+8]); pos += 8
        for b in d[pos:pos+ln]: chk ^= b
        pos += ln
    pos += 15 - (pos % 16)
    if d[pos] != chk: fail(f"{name}: checksum"); return
    pos += 1
    if hash_app:
        if hashlib.sha256(d[:pos]).digest() != d[pos:pos+32]: fail(f"{name}: sha256"); return
    print(f"  {name}: image OK ({nseg} segments, chip id {hdr[12]})")

for f in ("menu.bin", "meshtastic.bin", "meshcore.bin", "rnode.bin", "meshcore-repeater.bin"):
    verify_app(open(os.path.join(fw, f), "rb").read(), f)

# filesystem lookup rules
spiffs_sub = [p for p in parts if p[1] == 1 and p[2] == 0x82]
print("first SPIFFS-subtype partition (MeshCore/RNode SPIFFS.begin, label NULL):", spiffs_sub[0][0])
if spiffs_sub[0][0] != "fs_mc_rn": fail("shared SPIFFS must be the first spiffs partition")
if not any(p[0] == "spiffs" for p in parts): fail("Meshtastic LittleFS label 'spiffs' missing")
if [p for p in parts if p[0] == "nvs"][0][3] != 0x9000: fail("nvs not at 0x9000")

# otadata: emulate bootloader selection
apps = [p for p in parts if p[1] == 0 and 0x10 <= p[2] < 0x20]
def select(od):
    best = None
    for s in (0, 1):
        e = od[s*0x1000:s*0x1000+32]
        seq, = struct.unpack("<I", e[:4]); crc, = struct.unpack("<I", e[28:32])
        if seq == 0xFFFFFFFF: continue
        if binascii.crc32(e[:4], 0xFFFFFFFF) & 0xFFFFFFFF != crc: continue
        if best is None or seq > best: best = seq
    if best is None: return "factory"
    return apps[(best - 1) % len(apps)][0]
print("otadata initial ->", select(open(os.path.join(fw, "ota_data_initial.bin"), "rb").read()))
for n in (1, 2, 3, 4):
    r = select(m.otadata_for(n))
    print(f"switch {n} -> {r}")
    if r != ["meshtastic", "meshcore", "rnode", "repeater"][n-1]: fail("otadata mapping")

# names
print("menu names:", open(os.path.join(fw, "mbnames.bin"), "rb").read().split(b"\xff")[0].decode().split("\n")[:4])
print("RESULT:", "ALL OK" if ok else "PROBLEMS FOUND")
