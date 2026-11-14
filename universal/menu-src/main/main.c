/*
 * Universal multiboot menu for Heltec WiFi LoRa 32 V4 (factory partition).
 *
 * The PC installer stores in the "mbstate" partition:
 *   0x0000  header: slot names, offsets, sizes
 *   0x1000+k*0x1000  partition table variant for slot k
 *   0xF000  append-only log of the last started slot
 * Each slot has its own NVS and filesystem. Starting slot k writes table
 * variant k to 0x8000, where that slot's NVS/FS carry the standard labels
 * ("nvs", "spiffs"). Each firmware therefore sees only its own data.
 *
 * PRG short press = next, PRG long press = start, serial "1".."6" = start slot.
 */
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/usb_serial_jtag.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_flash.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_rom_crc.h"
#include "font6x10.h"

#define PIN_BTN      0
#define PIN_VEXT     36
#define PIN_OLED_RST 21
#define PIN_SDA      17
#define PIN_SCL      18
#define OLED_ADDR    0x3C
#define I2C_PORT     I2C_NUM_0

#define MAX_SLOTS    6
#define VISIBLE      4
#define SHORT_MAX_MS 600
#define LONG_MS      800
#define AUTOBOOT_S   15

#define PT_ADDR      0x8000
#define PT_LEN       0xC00
#define LOG_OFF      0xF000
#define LOG_LEN      0x1000

typedef struct __attribute__((packed)) {
    char     name[24];
    uint32_t app_off, app_size, nvs_off, nvs_size, fs_off, fs_size, table_off, flags;
} mb_slot_t;  /* 56 bytes, padded to 64 in flash */

typedef struct {
    mb_slot_t s;
    const esp_partition_t *part;
    bool valid;
} slot_t;

static const char *TAG = "menu";
static slot_t slots[MAX_SLOTS];
static int nslots = 0;
static const esp_partition_t *mbstate = NULL;
static uint8_t fb[128 * 8];
static bool oled_ok = false;
static const char *layout_err = NULL;

/* ---------------- SSD1306 ---------------- */

static esp_err_t i2c_send(uint8_t ctrl, const uint8_t *d, size_t n)
{
    i2c_cmd_handle_t h = i2c_cmd_link_create();
    i2c_master_start(h);
    i2c_master_write_byte(h, (OLED_ADDR << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(h, ctrl, true);
    i2c_master_write(h, d, n, true);
    i2c_master_stop(h);
    esp_err_t r = i2c_master_cmd_begin(I2C_PORT, h, pdMS_TO_TICKS(50));
    i2c_cmd_link_delete(h);
    return r;
}

static void oled_flush(void)
{
    if (!oled_ok) return;
    static const uint8_t win[] = {0x21, 0x00, 0x7F, 0x22, 0x00, 0x07};
    i2c_send(0x00, win, sizeof(win));
    for (int i = 0; i < (int)sizeof(fb); i += 128) i2c_send(0x40, &fb[i], 128);
}

static void oled_init(void)
{
    gpio_reset_pin(PIN_VEXT);
    gpio_set_direction(PIN_VEXT, GPIO_MODE_OUTPUT);
    gpio_set_level(PIN_VEXT, 0);
    vTaskDelay(pdMS_TO_TICKS(50));
    gpio_reset_pin(PIN_OLED_RST);
    gpio_set_direction(PIN_OLED_RST, GPIO_MODE_OUTPUT);
    gpio_set_level(PIN_OLED_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(PIN_OLED_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(20));

    i2c_config_t cfg = {
        .mode = I2C_MODE_MASTER, .sda_io_num = PIN_SDA, .scl_io_num = PIN_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE, .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 400000,
    };
    i2c_param_config(I2C_PORT, &cfg);
    i2c_driver_install(I2C_PORT, I2C_MODE_MASTER, 0, 0, 0);
    static const uint8_t init[] = {
        0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14,
        0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1,
        0xDB, 0x40, 0xA4, 0xA6, 0xAF
    };
    oled_ok = (i2c_send(0x00, init, sizeof(init)) == ESP_OK);
    if (!oled_ok) ESP_LOGW(TAG, "OLED not found");
}

static void oled_off(void)
{
    static const uint8_t off[] = {0xAE};
    if (oled_ok) i2c_send(0x00, off, 1);
}

static void px(int x, int y, bool on)
{
    if (x < 0 || x >= 128 || y < 0 || y >= 64) return;
    if (on) fb[(y / 8) * 128 + x] |= (1 << (y & 7));
    else    fb[(y / 8) * 128 + x] &= ~(1 << (y & 7));
}

static void fill(int x, int y, int w, int h, bool on)
{
    for (int j = y; j < y + h; j++) for (int i = x; i < x + w; i++) px(i, j, on);
}

static void text(int x, int y, const char *s, bool inv)
{
    for (; *s && x < 128; s++, x += 6) {
        char c = (*s < 32 || *s > 126) ? '?' : *s;
        const uint16_t *g = FONT6x10[c - 32];
        for (int cx = 0; cx < 6; cx++)
            for (int cy = 0; cy < 10; cy++) {
                bool on = (g[cx] >> cy) & 1;
                px(x + cx, y + cy, inv ? !on : on);
            }
    }
}

/* ---------------- layout ---------------- */

static bool load_layout(void)
{
    mbstate = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "mbstate");
    if (!mbstate) { layout_err = "no mbstate"; return false; }

    uint8_t hdr[8 + 64 * MAX_SLOTS + 4];
    esp_partition_read(mbstate, 0, hdr, sizeof(hdr));
    if (memcmp(hdr, "MBT2", 4) != 0) { layout_err = "run installer"; return false; }
    uint32_t crc_stored;
    memcpy(&crc_stored, &hdr[8 + 64 * MAX_SLOTS], 4);
    uint32_t crc = esp_rom_crc32_le(0, hdr, 8 + 64 * MAX_SLOTS);
    if (crc != crc_stored) { layout_err = "layout crc"; return false; }

    nslots = hdr[5];
    if (nslots < 1 || nslots > MAX_SLOTS) { layout_err = "bad slot count"; return false; }

    for (int i = 0; i < nslots; i++) {
        memcpy(&slots[i].s, &hdr[8 + 64 * i], sizeof(mb_slot_t));
        slots[i].s.name[sizeof(slots[i].s.name) - 1] = 0;
        slots[i].part = esp_partition_find_first(ESP_PARTITION_TYPE_APP,
                                                 ESP_PARTITION_SUBTYPE_APP_OTA_MIN + i, NULL);
        slots[i].valid = false;
        if (slots[i].part && slots[i].part->address == slots[i].s.app_off) {
            uint8_t magic = 0;
            esp_app_desc_t d;
            esp_partition_read(slots[i].part, 0, &magic, 1);
            slots[i].valid = (magic == 0xE9) &&
                             (esp_ota_get_partition_description(slots[i].part, &d) == ESP_OK);
        }
        printf("slot %d: %-20s app@0x%06x %s\n", i + 1, slots[i].s.name,
               slots[i].s.app_off, slots[i].valid ? "OK" : "EMPTY");
    }
    return true;
}

static int log_last(void)
{
    uint8_t buf[256];
    int last = -1;
    for (int off = 0; off < LOG_LEN; off += sizeof(buf)) {
        esp_partition_read(mbstate, LOG_OFF + off, buf, sizeof(buf));
        for (int i = 0; i < (int)sizeof(buf); i++) {
            if (buf[i] == 0xFF) return last;
            last = buf[i];
        }
    }
    return last;
}

static void log_append(uint8_t v)
{
    uint8_t buf[256];
    for (int off = 0; off < LOG_LEN; off += sizeof(buf)) {
        esp_partition_read(mbstate, LOG_OFF + off, buf, sizeof(buf));
        for (int i = 0; i < (int)sizeof(buf); i++) {
            if (buf[i] == 0xFF) {
                esp_partition_write(mbstate, LOG_OFF + off + i, &v, 1);
                return;
            }
        }
    }
    esp_partition_erase_range(mbstate, LOG_OFF, LOG_LEN);
    esp_partition_write(mbstate, LOG_OFF, &v, 1);
}

/* Write the partition table of slot i to 0x8000 (only if it differs). */
static bool install_table(int i)
{
    static uint8_t want[PT_LEN], have[PT_LEN];
    if (esp_partition_read(mbstate, slots[i].s.table_off, want, PT_LEN) != ESP_OK) return false;
    if (want[0] != 0xAA || want[1] != 0x50) return false;
    esp_flash_read(NULL, have, PT_ADDR, PT_LEN);
    if (memcmp(want, have, PT_LEN) == 0) return true;

    for (int attempt = 0; attempt < 3; attempt++) {
        if (esp_flash_erase_region(NULL, PT_ADDR, 0x1000) != ESP_OK) continue;
        if (esp_flash_write(NULL, want, PT_ADDR, PT_LEN) != ESP_OK) continue;
        esp_flash_read(NULL, have, PT_ADDR, PT_LEN);
        if (memcmp(want, have, PT_LEN) == 0) return true;
    }
    return false;
}

/* ---------------- UI ---------------- */

static int scroll = 0;

static void draw(int sel, int countdown, const char *msg)
{
    memset(fb, 0, sizeof(fb));
    fill(0, 0, 128, 10, true);
    char title[24];
    snprintf(title, sizeof(title), "MULTIBOOT %d/%d", nslots ? sel + 1 : 0, nslots);
    text(2, 0, title, true);

    if (sel < scroll) scroll = sel;
    if (sel >= scroll + VISIBLE) scroll = sel - VISIBLE + 1;
    for (int r = 0; r < VISIBLE && scroll + r < nslots; r++) {
        int i = scroll + r, y = 11 + r * 11;
        char line[24];
        snprintf(line, sizeof(line), "%d %.18s%s", i + 1, slots[i].s.name, slots[i].valid ? "" : " -");
        if (i == sel) { fill(0, y - 1, 128, 11, true); text(2, y, line, true); }
        else text(2, y, line, false);
    }
    if (scroll > 0) text(122, 11, "^", false);
    if (scroll + VISIBLE < nslots) text(122, 44, "v", false);

    char foot[24];
    if (msg) snprintf(foot, sizeof(foot), "%s", msg);
    else if (countdown >= 0) snprintf(foot, sizeof(foot), "auto start %2ds", countdown);
    else snprintf(foot, sizeof(foot), "short:next long:run");
    text(2, 54, foot, false);
    oled_flush();
}

static void boot_slot(int i)
{
    if (i < 0 || i >= nslots || !slots[i].valid) { printf("slot %d is empty\n", i + 1); return; }
    printf("starting slot %d (%s)\n", i + 1, slots[i].s.name);
    draw(i, -1, "starting...");
    if (!install_table(i)) {
        printf("partition table write failed\n");
        draw(i, -1, "table error!");
        vTaskDelay(pdMS_TO_TICKS(2000));
        return;
    }
    esp_err_t r = esp_ota_set_boot_partition(slots[i].part);
    if (r != ESP_OK) {
        printf("set_boot_partition: %s\n", esp_err_to_name(r));
        draw(i, -1, "image error!");
        vTaskDelay(pdMS_TO_TICKS(2000));
        return;
    }
    log_append((uint8_t)i);
    vTaskDelay(pdMS_TO_TICKS(200));
    oled_off();
    gpio_set_level(PIN_VEXT, 1);
    esp_restart();
}

void app_main(void)
{
    gpio_reset_pin(PIN_BTN);
    gpio_set_direction(PIN_BTN, GPIO_MODE_INPUT);
    gpio_set_pull_mode(PIN_BTN, GPIO_PULLUP_ONLY);
    usb_serial_jtag_driver_config_t ucfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    bool usb_ok = (usb_serial_jtag_driver_install(&ucfg) == ESP_OK);

    oled_init();
    bool ok = load_layout();
    if (!ok) {
        memset(fb, 0, sizeof(fb));
        text(2, 0, "MULTIBOOT", false);
        text(2, 20, "layout error:", false);
        text(2, 32, layout_err, false);
        oled_flush();
        while (1) {
            printf("layout error: %s\n", layout_err);
            vTaskDelay(pdMS_TO_TICKS(3000));
        }
    }

    int last = log_last();
    bool last_ok = (last >= 0 && last < nslots && slots[last].valid);
    int sel = last_ok ? last : 0;
    for (int k = 0; k < nslots && !slots[sel].valid; k++) sel = (sel + 1) % nslots;
    bool autoboot = last_ok;
    int64_t t0 = esp_timer_get_time();
    int shown_cd = -2;

    printf("\nMultiboot menu. Send 1-%d to start a firmware, l = list.\n", nslots);
    draw(sel, autoboot ? AUTOBOOT_S : -1, NULL);

    int64_t press_start = 0;
    bool pressed = false, long_fired = false;

    while (1) {
        bool now = gpio_get_level(PIN_BTN) == 0;
        int64_t t = esp_timer_get_time();

        if (now && !pressed) {
            pressed = true; long_fired = false; press_start = t; autoboot = false;
        } else if (now && pressed && !long_fired && (t - press_start) / 1000 >= LONG_MS) {
            long_fired = true;
            boot_slot(sel);
            draw(sel, -1, NULL);
        } else if (!now && pressed) {
            pressed = false;
            int ms = (int)((t - press_start) / 1000);
            if (!long_fired && ms >= 30 && ms < SHORT_MAX_MS) {
                sel = (sel + 1) % nslots;
                draw(sel, -1, NULL);
            }
        }

        if (usb_ok) {
            uint8_t c;
            if (usb_serial_jtag_read_bytes(&c, 1, 0) == 1) {
                if (c >= '1' && c < '1' + nslots) {
                    autoboot = false;
                    sel = c - '1';
                    boot_slot(sel);
                    draw(sel, -1, NULL);
                } else if (c == 'l' || c == 'L') {
                    load_layout();
                }
            }
        }

        if (autoboot) {
            int cd = AUTOBOOT_S - (int)((t - t0) / 1000000);
            if (cd <= 0) { autoboot = false; boot_slot(last); }
            else if (cd != shown_cd) { shown_cd = cd; draw(sel, cd, NULL); }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
