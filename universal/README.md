# Heltec V4 Universal Multiboot: 2–6 firmwares of your choice

**[Русская инструкция ниже ↓](#русский)**

A boot menu for the **Heltec WiFi LoRa 32 V4** (ESP32-S3, 16 MB). You pick 2 to 6 firmware files (Meshtastic, MeshCore companion/repeater/room server, RNode/Reticulum, your own builds and so on). The installer puts them all on the board. Switch between them with the PRG button or from the PC.

**Every slot gets its own NVS and its own filesystem.** When you start slot N, the menu writes that slot's partition table, in which slot N's storage carries the standard names (`nvs`, `spiffs`). Any firmware therefore runs unmodified and never sees another slot's settings, keys or contacts.

> ⚠️ **Experimental.** Offline checks pass (partition tables are validated with ESP-IDF's `gen_esp32part`, and the installer is simulated end to end). It has not been confirmed on real hardware yet. The installer offers a full backup first.

## Quick start

1. Install Python 3.8+.
2. Put your `.bin` files into `my-firmware/`. Both app-only ("update") files and merged/factory files work.
3. Run `install.bat` (Windows) or `./install.sh` (Linux/macOS). The wizard asks for the order and the menu names, makes a backup and installs.

Or by hand:

```bash
pip install esptool pyserial
cp slots.example.txt slots.txt         # edit: name | file | options
python multiboot.py plan slots.txt     # show the layout
python multiboot.py install slots.txt
```

## Switching

- **On the board:** tap **RST**. While the white LED is on (~1.5 s), hold **PRG** until it blinks. In the menu, a short press moves to the next item and a long press starts it. Without a press, the last used firmware starts after 15 s.
- **From the PC:** `python multiboot.py switch 3`, or `python multiboot.py menu` to open the menu.

## Managing slots

| Command | What it does |
|---|---|
| `list` | show the slots on the board |
| `update N file.bin` | new version of the same firmware, settings kept |
| `replace N file.bin [Name]` | put a different firmware into slot N, slot data erased |
| `wipe N` | erase slot N's settings and filesystem |
| `names "A" "B" ...` | rename the menu entries |
| `backup` / `restore file.bin` | full 16 MB dump / restore |
| `hash file.bin` | firmware hash (RNode: `rnodeconf PORT --firmware-hash ...`) |

Options in `slots.txt`: `app=3M` (app slot size), `fs=1M` (filesystem size), `fsimg=image.bin` (flash a filesystem image; the fs size becomes the image size), `label=spiffs` and `fstype=spiffs|littlefs|fat` (for firmwares that look for another partition, e.g. `label=ffat,fstype=fat`).
Free space is shared between the app slots, so a slot can later take a bigger or a different firmware.

## Memory layout

```
0x000000 bootloader (ESP-IDF 4.4.8 + hook: RST, then hold PRG -> menu)
0x008000 partition table   <- rewritten by the menu for the selected slot
0x009000 (unused)          0x00E000 otadata (erased = menu)
0x010000 menu (factory)    0x060000 mbstate (slot list + per-slot tables + last used)
0x070000 slot 1: app | nvs | fs     slot 2: app | nvs | fs   ... up to slot 6
```

## Limitations

- Do not use a firmware's own OTA update (phone app, Wi-Fi `start ota`, web-flasher "update", `rnodeconf --autoinstall`). Update through `multiboot.py update`.
- All firmwares share the board's Bluetooth MAC. The phone may need to "forget" the device after a switch.
- RNode needs a one-time `rnodeconf` provisioning (`--key`, `--rom --product c3 --model c8 --hwrev 1`, `--firmware-hash <multiboot.py hash>`).
- Each firmware keeps its own region settings. Set 868 MHz in each one.
- If power is lost during the ~50 ms partition-table write, the board needs a USB reinstall (`install`). Your firmwares in the other slots stay intact.

Build the menu: ESP-IDF v4.4.8, `cd menu-src && idf.py set-target esp32s3 && idf.py build`. Offline layout check: `python tools/verify.py slots.txt`.

License: MIT. Firmware you install keeps its own license.

---

## Русский

Меню загрузки для **Heltec WiFi LoRa 32 V4**. Ты сама выбираешь от 2 до 6 прошивок: Meshtastic, MeshCore (компаньон, репитер, room server), RNode/Reticulum, свои сборки. Все они ставятся на плату одновременно, переключаться можно кнопкой или с компьютера.

**У каждого слота свой NVS и своя файловая система.** Когда ты запускаешь слот N, меню записывает таблицу разделов именно этого слота, и прошивка видит только свои данные. Поэтому подходят любые прошивки без доработки.

> ⚠️ **Экспериментальная сборка.** Офлайн всё проверено: таблицы разделов проходят проверку утилитой ESP-IDF, установка смоделирована от начала до конца. На реальной плате ещё не запускалась. Перед установкой мастер предложит сделать резервную копию.

### Установка

1. Поставь Python 3.8+ (на Windows при установке отметь «Add to PATH»).
2. Положи свои `.bin` в папку `my-firmware/`. Подходят и обычные (update), и полные (factory/merged) файлы.
3. Запусти `install.bat` (Windows) или `./install.sh` (Linux/macOS). Мастер спросит порядок и названия, сделает резервную копию и установит.

Настройки сохраняются в `slots.txt`. Его можно поправить вручную и поставить заново: `python multiboot.py install slots.txt`. Чтобы сначала посмотреть раскладку памяти, без записи на плату: `python multiboot.py plan slots.txt`.

### Переключение

- **Кнопками:** нажми и отпусти **RST**. Пока горит белый светодиод, зажми **PRG** и держи, пока он не мигнёт. Откроется меню: короткое нажатие — следующий пункт, долгое — запуск. Если ничего не нажимать, через 15 секунд запустится прошивка, которая работала последней.
- **С компьютера:** `python multiboot.py switch 3`. Открыть меню: `python multiboot.py menu`.

### Управление слотами

- `list` — что стоит на плате.
- `update N файл.bin` — новая версия той же прошивки, настройки сохраняются.
- `replace N файл.bin [Название]` — другая прошивка в слот N, данные слота стираются.
- `wipe N` — стереть настройки слота N.
- `names "А" "Б" ...` — переименовать пункты меню.
- `backup` / `restore файл.bin` — полная копия памяти и восстановление из неё.

Опции в `slots.txt`:
- `app=3M` — размер слота под прошивку;
- `fs=1M` — размер файловой системы;
- `fsimg=файл.bin` — записать образ ФС, например веб-консоль RNode;
- `label=` и `fstype=` — для прошивок, которые ищут раздел с другим именем.

### Ограничения

- **Не обновляй прошивки их собственным OTA.** Обновлять только через `multiboot.py update`.
- **Bluetooth.** У всех прошивок один MAC-адрес. После переключения телефон может потребовать заново спарить плату.
- **RNode** нужно один раз настроить через `rnodeconf`. Хеш прошивки для этого выдаёт команда `python multiboot.py hash rnode.bin`.
- **Регион.** Каждая прошивка хранит его отдельно, в каждой выставь 868 МГц.
- **Питание при переключении.** Если оно пропадёт во время записи таблицы разделов (около 50 мс), плату придётся переустановить по USB. Прошивки в остальных слотах при этом не пострадают.
