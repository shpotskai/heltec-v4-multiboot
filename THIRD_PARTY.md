# Third-party firmware in `firmware/`

These binaries are redistributed **unmodified**, except where noted. The complete corresponding source code is available at the tags and commits below.

| File | Project | License | Source (exact version) |
|------|---------|---------|------------------------|
| `meshtastic.bin`, `meshtastic-littlefs.bin` | Meshtastic firmware 2.8.2.4c89b69, target `heltec-v4` | GPL-3.0 | https://github.com/meshtastic/firmware/tree/4c89b6937cc5073fc71fa825dfcca7c9f18becee — release asset `firmware-esp32s3-2.8.2.4c89b69.zip` |
| `meshcore.bin` | MeshCore companion-v1.18.0, env `heltec_v4_companion_radio_ble` | MIT | https://github.com/meshcore-dev/MeshCore/tree/81dbae592ffad40b4212bc0b37e82b38da08f4bc |
| `meshcore-repeater.bin` | MeshCore repeater-v1.18.0, env `heltec_v4_repeater` — **modified**: strings `/identity`→`/idrepeat`, `/prefs.json`→`/rprfs.json`, checksum/SHA-256 recomputed (see `patch_repeater()` in `multiboot.py`) | MIT | https://github.com/meshcore-dev/MeshCore/tree/81dbae592ffad40b4212bc0b37e82b38da08f4bc |
| `rnode.bin`, `rnode-console-spiffs.bin` | RNode Firmware 1.86, `rnode_firmware_heltec32v4pa.zip` | GPL-3.0 | https://github.com/markqvist/RNode_Firmware/tree/9b39b6ce5962007fafefc22034082f354eff3374 |
| `bootloader.bin`, `menu.bin` | built from `menu-src/` with ESP-IDF v4.4.8 | MIT (this repo) + Apache-2.0 (ESP-IDF) | https://github.com/espressif/esp-idf/tree/v4.4.8 |

The original upstream release files can be downloaded from the projects' GitHub release pages. Full license texts are in each upstream repository.
