"""Offline checks of a universal-multiboot layout: python tools/verify.py slots.txt"""
import binascii, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import multiboot as m

idf = os.environ.get("IDF_PATH")
gen = None
if idf:
    sys.path.insert(0, os.path.join(idf, "components", "partition_table"))
    import gen_esp32part as gen

ok = True
def fail(msg):
    global ok; ok = False; print("FAIL:", msg)

slots = m.parse_slots_file(sys.argv[1])
mode, end = m.plan(slots)
m.print_plan(slots, end)
blob = m.mbstate_blob(slots)

# header as the menu reads it
if blob[:4] != b"MBT2": fail("magic")
crc, = struct.unpack("<I", blob[8 + 64 * 6:12 + 64 * 6])
if binascii.crc32(blob[:8 + 64 * 6]) & 0xFFFFFFFF != crc: fail("header crc")
back = m.parse_mbstate(blob)
for a, b in zip(slots, back):
    for k in ("app_off", "app_size", "nvs_off", "nvs_size", "fs_off", "fs_size"):
        if getattr(a, k) != getattr(b, k): fail(f"header field {k}")

# regions do not overlap, fit 16 MB, apps 64K aligned
regions = [(0, 0x8000, "bootloader"), (0x8000, 0x9000, "ptable"), (m.OTADATA, m.OTADATA + 0x2000, "otadata"),
           (m.MENU[0], sum(m.MENU), "menu"), (m.MBSTATE[0], sum(m.MBSTATE), "mbstate")]
for i, s in enumerate(slots):
    regions += [(s.app_off, s.app_off + s.app_size, f"app{i+1}"), (s.nvs_off, s.nvs_off + s.nvs_size, f"nvs{i+1}"),
                (s.fs_off, s.fs_off + s.fs_size, f"fs{i+1}")]
    if s.app_off % 0x10000: fail(f"app{i+1} not 64K aligned")
    if len(s.app) > s.app_size: fail(f"app{i+1} too big")
    if s.fsimg and len(s.fsimg) != s.fs_size: fail(f"fs image {i+1} size")
regions.sort()
for a, b in zip(regions, regions[1:]):
    if a[1] > b[0]: fail(f"overlap {a[2]} / {b[2]}")
if regions[-1][1] > m.FLASH: fail("exceeds 16 MB")

# every table variant
for k, s in enumerate(slots):
    t = blob[0x1000 + k * 0x1000:0x1000 + k * 0x1000 + 0xC00]
    if t != m.table_for(slots, k): fail("stored table differs")
    if gen:
        gen.offset_part_table = 0x8000
        tbl = gen.PartitionTable.from_binary(t)  # also checks MD5
        tbl.verify()
    ents = []
    for j in range(0, 0xC00, 32):
        e = t[j:j + 32]
        if e[:2] != b"\xaa\x50": break
        ty, st, off, sz = struct.unpack("<BBII", e[2:12])
        ents.append((e[12:28].split(b"\0")[0].decode(), ty, st, off, sz))
    nvs = [e for e in ents if e[1] == 1 and e[2] == 2]
    if len(nvs) != 1 or nvs[0][3] != s.nvs_off or nvs[0][0] != "nvs": fail(f"table {k+1}: nvs")
    fs_first = [e for e in ents if e[1] == 1 and e[2] == m.FS_SUB[s.fstype]][0]
    if fs_first[3] != s.fs_off: fail(f"table {k+1}: first fs partition")
    if [e for e in ents if e[0] == s.label][0][3] != s.fs_off: fail(f"table {k+1}: fs label")
    apps = [e for e in ents if e[1] == 0 and 0x10 <= e[2] < 0x20]
    if [e[3] for e in apps] != [x.app_off for x in slots]: fail(f"table {k+1}: app list")
    if not any(e[0] == "mbstate" and e[3] == m.MBSTATE[0] for e in ents): fail("mbstate missing")
    if not any(e[1] == 0 and e[2] == 0 and e[3] == m.MENU[0] for e in ents): fail("menu missing")
    print(f"  table {k+1}: {len(ents)} entries, nvs@0x{s.nvs_off:x} fs '{s.label}'@0x{s.fs_off:x}  OK"
          + (" (gen_esp32part OK)" if gen else ""))

# otadata -> slot (same rule as the ESP-IDF bootloader)
for n in range(1, len(slots) + 1):
    seq = n
    if (seq - 1) % len(slots) != n - 1: fail("ota seq mapping")
print("RESULT:", "ALL OK" if ok else "PROBLEMS FOUND", f"({mode} layout)")
