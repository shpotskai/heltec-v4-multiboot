# Мультибут для Heltec WiFi LoRa 32 V4 (868 МГц)

**[English version → README.md](README.md)**

Четыре прошивки на одной плате, переключение кнопкой прямо на устройстве или командой с компьютера:

| № | Прошивка | Версия | Слот |
|---|----------|--------|------|
| 1 | Meshtastic | 2.8.2.4c89b69 (heltec-v4) | 0x070000, 3 МБ |
| 2 | MeshCore Companion BLE | v1.18.0 (heltec_v4_companion_radio_ble) | 0x370000, 2.5 МБ |
| 3 | RNode / Reticulum | 1.86 (heltec32v4pa) | 0x5F0000, 2 МБ |
| 4 | MeshCore Repeater | v1.18.0 (heltec_v4_repeater) | 0xD50000, 2 МБ |

Сборка для **обычной V4 (V4.2 / V4.3, 2 МБ PSRAM)**. Для V4-R8 не подходит.

Прошивки 1–3 официальные, без изменений. В репитере изменены только два имени файлов: `/identity` → `/idrepeat` и `/prefs.json` → `/rprfs.json`. Благодаря этому у репитера и компаньона MeshCore разные ключи (идентичность) и разные настройки, хотя файловая система у них общая. Отличаются только загрузчик, меню и таблица разделов.

> ⚠️ **Экспериментальная сборка.** Разметка памяти, образы прошивок и логика переключения проверены офлайн (`menu-src/verify.py`). На реальной плате работа пока не подтверждена. Перед установкой обязательно сделай резервную копию (`install` предложит её сам).

---

## Что нужно

- Компьютер с Python 3.8+ (Windows: python.org, при установке отметь «Add to PATH»)
- `pip install esptool`
- Для RNode дополнительно: `pip install rns`
- USB-C кабель с данными

## Установка (один раз)

1. **Сделай резервную копию** текущей прошивки:
   ```
   python multiboot.py backup
   ```
   Получится файл `backup-heltec-v4-ДАТА.bin` (16 МБ). Вернуть всё как было: `python multiboot.py restore файл.bin`.

2. **Установи мультибут** (стирает всю память платы):
   ```
   python multiboot.py install
   ```
   На Windows можно запустить `install.bat`, на Linux/macOS — `./install.sh`.
   Если порт не нашёлся сам, укажи его: `python multiboot.py -p COM5 install`.
   Если esptool не может подключиться: зажми **PRG**, нажми и отпусти **RST**, отпусти **PRG**, повтори команду.

3. После установки на экране появится меню.

4. **RNode один раз провизионируй** (без этого RNode пишет на экране «firmware corrupt / not provisioned»):
   ```
   python multiboot.py switch 3
   python multiboot.py rnode
   ```
   Вторая команда выведет готовые команды `rnodeconf`. Выполни их по порядку.

---

## Как переключаться

### Кнопками на плате

1. Нажми и отпусти **RST**.
2. Как только загорится **белый светодиод** (горит ~1.5 с), **зажми PRG** и держи, пока он не мигнёт 3 раза.
3. Откроется меню:
   - **короткое нажатие PRG** — следующая прошивка;
   - **долгое нажатие PRG (~1 с)** — запустить выбранную.
4. Если в меню ничего не нажимать, через 15 секунд запустится прошивка, которая работала последней.

Выбранная прошивка запоминается: дальше плата всегда стартует в неё (и после выключения питания), пока снова не выберешь другую.

> Важно: PRG нужно зажимать **после** отпускания RST. Если держать PRG, когда отпускаешь RST, плата уйдёт в режим прошивки (экран тёмный). Тогда просто нажми RST ещё раз.

### С компьютера по USB

```
python multiboot.py switch 1     # Meshtastic
python multiboot.py switch 2     # MeshCore
python multiboot.py switch 3     # RNode
python multiboot.py switch 4     # MeshCore Repeater
python multiboot.py menu         # открыть меню на плате
```

Ещё можно в открытом меню отправить по USB-серийнику символ `1`–`4`.

---

## Обновление прошивки в слоте

Нужен **app-only .bin** (у Meshtastic это `firmware-heltec-v4-ВЕРСИЯ.bin`, не `.factory.bin`; у MeshCore — файл без `-merged`; у RNode — `rnode_firmware_heltec32v4pa.bin` из zip). Настройки сохраняются.

```
python multiboot.py update 1 firmware-heltec-v4-2.8.3.xxxx.bin
python multiboot.py update 2 heltec_v4_companion_radio_ble-v1.19.0-xxxx.bin
python multiboot.py update 3 rnode_firmware_heltec32v4pa.bin
python multiboot.py update 4 heltec_v4_repeater-v1.19.0-xxxx.bin   # патч применится автоматически
```

Подписи в меню меняются так: `python multiboot.py names "Meshtastic" "MeshCore" "RNode" "Repeater"`.

После обновления RNode выполни `python multiboot.py rnode --hash-only` и команду, которую он выведет.

---

## Ограничения (важно)

- **Не обновляй прошивки их собственным OTA** (по Wi-Fi или BLE из приложения, веб-флешером в режиме «update», `rnodeconf --autoinstall`). Они пишут в соседний слот или переразмечают память, и мультибут сломается. Обновляй только через `multiboot.py update`. Если что-то сломалось, повтори `install`.
- **Репитер MeshCore:** команду `start ota` не использовать. Она перепишет слот Meshtastic. Регион и частоту репитера настраивай через USB-консоль (`set freq 869.525` и т.д.) или удалённо через приложение MeshCore.
- **Bluetooth.** У всех трёх прошивок один и тот же MAC-адрес. После переключения телефон может не подключиться к плате. Тогда удали её в настройках Bluetooth телефона («Забыть устройство») и спарь заново.
- **Хранилища:**
  - у Meshtastic своя LittleFS (3.4 МБ);
  - у MeshCore (компаньон и репитер) и RNode общий SPIFFS (1.9 МБ). Имена файлов не пересекаются. Но если сделать в MeshCore полное стирание/форматирование, сотрутся данные всех троих;
  - раздел NVS общий для всех. В нём лежит провизионирование RNode (EEPROM).
- **Полный сброс Meshtastic** (Factory reset device) может стереть общий NVS. Тогда RNode придётся провизионировать заново (шаг 4), а телефон, возможно, заново спарить.
- Каждая прошивка выставляет регион и частоту сама. Проверь, что везде стоит 868 МГц (EU_868).

---

## Как это устроено

```
0x000000  bootloader (ESP-IDF 4.4.8 + хук: RST → держать PRG → меню)
0x008000  таблица разделов
0x009000  nvs         общий
0x00E000  otadata     какой слот грузить (стёрт → меню)
0x010000  menu        меню на OLED (factory)
0x070000  meshtastic  ota_0
0x370000  meshcore    ota_1
0x5F0000  rnode       ota_2
0x7F0000  fs_mc_rn    SPIFFS MeshCore + RNode (первый spiffs-раздел)
0x9D0000  spiffs      LittleFS Meshtastic (ищет раздел по имени "spiffs")
0xD30000  mbnames     подписи в меню
0xD31000  coredump
0xD50000  repeater    ota_3
```

Исходники меню и загрузчика лежат в `menu-src/`. Сборка: ESP-IDF v4.4.8, `idf.py build`.
