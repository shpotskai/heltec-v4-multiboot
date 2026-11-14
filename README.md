# Heltec V4 Multiboot — Meshtastic + MeshCore + Reticulum (RNode) on one board

**[Русская версия / инструкция на русском → README.ru.md](README.ru.md)**

> 🆕 **Want to choose your own firmwares (2–6 of any kind)?** Use the **[universal multiboot → `universal/`](universal/)**. Put your `.bin` files in, run the wizard, and every slot gets its own settings and filesystem.
> **Хочешь сама выбрать прошивки (2–6 любых)?** Тебе нужна **[универсальная версия → `universal/`](universal/)**.

Multiboot firmware package for the **Heltec WiFi LoRa 32 V4** (ESP32-S3, SX1262, 16 MB flash).
Four firmwares live on the board at the same time. Switch between them with the PRG button and an on-screen menu, or with one command over USB. No reflashing needed.

| # | Firmware | Version | Notes |
|---|----------|---------|-------|
| 1 | [Meshtastic](https://github.com/meshtastic/firmware) | 2.8.2.4c89b69 (`heltec-v4`) | own LittleFS |
| 2 | [MeshCore](https://github.com/meshcore-dev/MeshCore) Companion BLE | 1.18.0 (`heltec_v4_companion_radio_ble`) | |
| 3 | [RNode](https://github.com/markqvist/RNode_Firmware) for [Reticulum](https://reticulum.network) | 1.86 (`heltec32v4pa`) | needs one-time `rnodeconf` provisioning |
| 4 | MeshCore Repeater | 1.18.0 (`heltec_v4_repeater`) | separate identity from the companion |

> ⚠️ **Experimental.** The image layout, app images and boot-slot logic are verified offline (`menu-src/verify.py`), but the package has **not yet been confirmed on real hardware**. Make a full backup first (the installer does it for you). Reports and PRs are welcome.

Target: **standard Heltec V4 (V4.2 / V4.3, 2 MB PSRAM)**. Not for V4-R8 / TFT variants.

---

## Quick start

Requirements: Python 3.8+, `pip install esptool`, a USB-C data cable.

```bash
git clone https://github.com/shpotskai/heltec-v4-multiboot
cd heltec-v4-multiboot
python multiboot.py backup      # full 16 MB dump of what is on the board now
python multiboot.py install     # erases the chip and installs everything
```

Windows: double-click `install.bat`. Linux/macOS: run `./install.sh`. Both make a backup first.
To pick the serial port yourself, add `-p COM5` or `-p /dev/ttyACM0`. If the board does not connect, hold **PRG**, tap **RST**, then release **PRG**.

RNode needs one-time provisioning:

```bash
pip install rns
python multiboot.py switch 3
python multiboot.py rnode       # prints the exact rnodeconf commands
```

## Switching firmware

**On the device:** tap **RST**. While the white LED is lit (~1.5 s), press and hold **PRG** until the LED blinks. The menu opens:
- short PRG press: next item
- long PRG press (~1 s): start the selected firmware

If no button is pressed, the last used firmware starts after 15 s. The chosen firmware stays selected across power cycles.

**Over USB:**

```bash
python multiboot.py switch 1   # Meshtastic
python multiboot.py switch 2   # MeshCore companion
python multiboot.py switch 3   # RNode / Reticulum
python multiboot.py switch 4   # MeshCore repeater
python multiboot.py menu       # open the on-device menu
```

## Updating one firmware (settings are kept)

Use the **app-only** `.bin` file, not a merged or factory image:

```bash
python multiboot.py update 1 firmware-heltec-v4-<ver>.bin
python multiboot.py update 2 heltec_v4_companion_radio_ble-<ver>.bin
python multiboot.py update 3 rnode_firmware_heltec32v4pa.bin     # then: multiboot.py rnode --hash-only
python multiboot.py update 4 heltec_v4_repeater-<ver>.bin        # patched automatically (see below)
python multiboot.py names "Meshtastic" "MeshCore" "RNode" "Repeater"
```

Do **not** use the firmwares' own OTA (phone app, Wi-Fi `start ota`, web flasher "update", `rnodeconf --autoinstall`). It writes into a neighbouring slot or rewrites the partition table.

## How it works

```
0x000000  bootloader   ESP-IDF 4.4.8 + hook: after power-on/RST, PRG held -> erase otadata -> menu
0x008000  partition table
0x009000  nvs          shared (RNode EEPROM, BLE bonds, PHY calibration)
0x00E000  otadata      selects the slot to boot; erased = menu
0x010000  menu         factory app, SSD1306 OLED menu
0x070000  meshtastic   ota_0  3 MB
0x370000  meshcore     ota_1  2.5 MB
0x5F0000  rnode        ota_2  2 MB
0x7F0000  fs_mc_rn     SPIFFS shared by MeshCore + RNode (first spiffs-subtype partition)
0x9D0000  spiffs       Meshtastic LittleFS (Meshtastic looks it up by the label "spiffs")
0xD30000  mbnames      menu labels
0xD31000  coredump
0xD50000  repeater     ota_3  2 MB
```

- The firmwares need no rebuilding. Arduino 2.x `SPIFFS.begin()` (MeshCore, RNode) mounts the **first** spiffs-subtype partition. Arduino 3.x `LittleFS.begin()` (Meshtastic) mounts the partition **labelled** `spiffs`. Partition order and labels alone keep the filesystems apart.
- Images 1–3 are the unmodified upstream release binaries.
- In the MeshCore **repeater** image, two file names are changed (`/identity` → `/idrepeat`, `/prefs.json` → `/rprfs.json`), and the checksum and SHA-256 are recomputed. The repeater shares SPIFFS with the companion, so without this it would also share its identity and settings. `multiboot.py update 4` applies the same patch to new repeater releases.
- The bootloader hook only waits for the button after a power-on/RST reset. Deep-sleep wakeups and software resets are not delayed.

## Known limitations

- All firmwares use the same Bluetooth MAC. After switching, the phone may need to "forget" the device and pair again.
- NVS is shared. A Meshtastic full factory reset may erase RNode provisioning; if so, run `multiboot.py rnode` again.
- A full MeshCore erase/format also wipes the RNode web-console files and the repeater identity, because they share SPIFFS.
- Each firmware keeps its own region settings. Set EU_868 / 869.525 MHz (or your region) in each one.

## Building the menu and bootloader

```bash
# ESP-IDF v4.4.8
cd menu-src
idf.py set-target esp32s3
idf.py build
python verify.py ..        # offline checks of the whole package
```

## Licenses and sources

The code in this repository (menu, bootloader hook, `multiboot.py`, scripts) is MIT licensed, see [LICENSE](LICENSE).
The bundled firmware binaries keep their own licenses. Exact sources are listed in [THIRD_PARTY.md](THIRD_PARTY.md).

Not affiliated with Heltec, Meshtastic, MeshCore or Reticulum.

**Keywords:** heltec v4 multiboot, dual boot meshtastic meshcore, esp32-s3 multi firmware, reticulum rnode heltec v4, lora 868 multiboot
