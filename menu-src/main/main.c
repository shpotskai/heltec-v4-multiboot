/*
 * Multiboot menu for Heltec WiFi LoRa 32 V4 (factory partition).
 *
 * PRG short press  -> next firmware
 * PRG long press   -> start selected firmware
 * USB serial: send 1-4 to start a slot, "l" to list.
 * If a firmware was used before, it starts automatically after a countdown
 * unless a button is pressed.
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
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "font6x10.h"

#define PIN_BTN      0
#define PIN_VEXT     36   /* LOW = OLED power on */
#define PIN_OLED_RST 21
#define PIN_SDA      17
#define PIN_SCL      18
#define PIN_LED      35
#define OLED_ADDR    0x3C
#define I2C_PORT     I2C_NUM_0

#define MAX_SLOTS    4
#define SHORT_MAX_MS 600
#define LONG_MS      800
#define AUTOBOOT_S   15

static const char *TAG = "menu";

typedef struct {
    const esp_partition_t *part;
    char name[24];
    bool valid;
} slot_t;

static slot_t slots[MAX_SLOTS];
static int nslots = 0;
static uint8_t fb[128 * 8];
static bool oled_ok = false;
static bool nvs_ok = false;

/* ---------------- SSD1306 ---------------- */

static esp_err_t oled_cmds(const uint8_t *c, size_t n)
{
    i2c_cmd_handle_t h = i2c_cmd_link_create();
    i2c_master_start(h);
    i2c_master_write_byte(h, (OLED_ADDR << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(h, 0x00, true);
    i2c_master_write(h, c, n, true);
    i2c_master_stop(h);
    esp_err_t r = i2c_master_cmd_begin(I2C_PORT, h, pdMS_TO_TICKS(50));
    i2c_cmd_link_delete(h);
    return r;
}

static void oled_flush(void)
{
    if (!oled_ok) return;
    static const uint8_t win[] = {0x21, 0x00, 0x7F, 0x22, 0x00, 0x07};
    oled_cmds(win, sizeof(win));
    for (int i = 0; i < (int)sizeof(fb); i += 128) {
        i2c_cmd_handle_t h = i2c_cmd_link_create();
        i2c_master_start(h);
        i2c_master_write_byte(h, (OLED_ADDR << 1) | I2C_MASTER_WRITE, true);
        i2c_master_write_byte(h, 0x40, true);
        i2c_master_write(h, &fb[i], 128, true);
        i2c_master_stop(h);
        i2c_master_cmd_begin(I2C_PORT, h, pdMS_TO_TICKS(50));
        i2c_cmd_link_delete(h);
    }
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
        .mode = I2C_MODE_MASTER,
        .sda_io_num = PIN_SDA,
        .scl_io_num = PIN_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 400000,
    };
    i2c_param_config(I2C_PORT, &cfg);
    i2c_driver_install(I2C_PORT, I2C_MODE_MASTER, 0, 0, 0);

    static const uint8_t init[] = {
        0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14,
        0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1,
        0xDB, 0x40, 0xA4, 0xA6, 0xAF
    };
    oled_ok = (oled_cmds(init, sizeof(init)) == ESP_OK);
    if (!oled_ok) ESP_LOGW(TAG, "OLED not found");
}

static void oled_off(void)
{
    if (!oled_ok) return;
    static const uint8_t off[] = {0xAE};
    oled_cmds(off, 1);
}

static void px(int x, int y, bool on)
{
    if (x < 0 || x >= 128 || y < 0 || y >= 64) return;
    if (on) fb[(y / 8) * 128 + x] |= (1 << (y & 7));
    else    fb[(y / 8) * 128 + x] &= ~(1 << (y & 7));
}

static void fill(int x, int y, int w, int h, bool on)
{
    for (int j = y; j < y + h; j++)
        for (int i = x; i < x + w; i++) px(i, j, on);
}

static void text(int x, int y, const char *s, bool inv)
{
    for (; *s; s++, x += 6) {
        char c = *s;
        if (c < 32 || c > 126) c = '?';
        const uint16_t *g = FONT6x10[c - 32];
        for (int cx = 0; cx < 6; cx++)
            for (int cy = 0; cy < 10; cy++) {
                bool on = (g[cx] >> cy) & 1;
                px(x + cx, y + cy, inv ? !on : on);
            }
    }
}

/* ---------------- slots ---------------- */

static void load_names(void)
{
    static const char *defaults[MAX_SLOTS] = {"Meshtastic", "MeshCore", "RNode/Reticulum", "MeshCore Repeater"};
    char buf[256] = {0};
    const esp_partition_t *np = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "mbnames");
    if (np) esp_partition_read(np, 0, buf, sizeof(buf) - 1);

    char *p = buf;
    for (int i = 0; i < MAX_SLOTS; i++) {
        const char *src = defaults[i];
        char line[24] = {0};
        if (p && (unsigned char)*p != 0xFF && *p) {
            char *nl = strchr(p, '\n');
            size_t len = nl ? (size_t)(nl - p) : strlen(p);
            if (len > 0 && len < sizeof(line)) {
                memcpy(line, p, len);
                bool printable = true;
                for (size_t k = 0; k < len; k++) if (line[k] < 32 || line[k] > 126) printable = false;
                if (printable) src = line;
            }
            p = nl ? nl + 1 : NULL;
        }
        strlcpy(slots[i].name, src, sizeof(slots[i].name));
    }
}

static void scan_slots(void)
{
    load_names();
    nslots = 0;
    for (int i = 0; i < MAX_SLOTS; i++) {
        const esp_partition_t *p = esp_partition_find_first(
            ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_MIN + i, NULL);
        slots[i].part = p;
        slots[i].valid = false;
        if (p) {
            uint8_t magic = 0;
            esp_app_desc_t d;
            esp_partition_read(p, 0, &magic, 1);
            slots[i].valid = (magic == 0xE9) && (esp_ota_get_partition_description(p, &d) == ESP_OK);
        }
        nslots = i + 1;
        printf("slot %d: %-16s %s %s\n", i + 1, slots[i].name,
               p ? p->label : "-", slots[i].valid ? "OK" : "EMPTY");
    }
}

/* ---------------- NVS (last used) ---------------- */

static int load_last(void)
{
    if (!nvs_ok) return -1;
    nvs_handle_t h;
    int8_t v = -1;
    if (nvs_open("mboot", NVS_READONLY, &h) == ESP_OK) {
        nvs_get_i8(h, "last", &v);
        nvs_close(h);
    }
    return v;
}

static void save_last(int v)
{
    if (!nvs_ok) return;
    nvs_handle_t h;
    if (nvs_open("mboot", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_i8(h, "last", (int8_t)v);
        nvs_commit(h);
        nvs_close(h);
    }
}

/* ---------------- UI ---------------- */

static void draw(int sel, int countdown, const char *msg)
{
    memset(fb, 0, sizeof(fb));
    fill(0, 0, 128, 10, true);
    text(2, 0, "MULTIBOOT  V4", true);
    for (int i = 0; i < nslots; i++) {
        int y = 11 + i * 11;
        char line[24];
        snprintf(line, sizeof(line), "%d %s", i + 1, slots[i].name);
        if (!slots[i].valid) {
            snprintf(line, sizeof(line), "%d %.12s -", i + 1, slots[i].name);
        }
        if (i == sel) {
            fill(0, y - 1, 128, 11, true);
            text(2, y, line, true);
        } else {
            text(2, y, line, false);
        }
    }
    char foot[24];
    if (msg) {
        snprintf(foot, sizeof(foot), "%s", msg);
    } else if (countdown >= 0) {
        snprintf(foot, sizeof(foot), "auto start %2ds", countdown);
    } else {
        snprintf(foot, sizeof(foot), "short:next long:run");
    }
    text(2, 54, foot, false);
    oled_flush();
}

static void boot_slot(int i)
{
    if (i < 0 || i >= nslots || !slots[i].valid) {
        printf("slot %d is empty\n", i + 1);
        return;
    }
    printf("starting slot %d (%s)\n", i + 1, slots[i].name);
    draw(i, -1, "starting...");
    esp_err_t r = esp_ota_set_boot_partition(slots[i].part);
    if (r != ESP_OK) {
        printf("set_boot_partition failed: %s\n", esp_err_to_name(r));
        draw(i, -1, "image error!");
        vTaskDelay(pdMS_TO_TICKS(2000));
        return;
    }
    save_last(i);
    vTaskDelay(pdMS_TO_TICKS(300));
    oled_off();
    gpio_set_level(PIN_VEXT, 1);
    esp_restart();
}

void app_main(void)
{
    esp_err_t e = nvs_flash_init();
    nvs_ok = (e == ESP_OK); /* never erase: NVS is shared with the firmwares */

    gpio_reset_pin(PIN_BTN);
    gpio_set_direction(PIN_BTN, GPIO_MODE_INPUT);
    gpio_set_pull_mode(PIN_BTN, GPIO_PULLUP_ONLY);

    usb_serial_jtag_driver_config_t ucfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    bool usb_ok = (usb_serial_jtag_driver_install(&ucfg) == ESP_OK);

    oled_init();
    scan_slots();

    int last = load_last();
    int sel = (last >= 0 && last < nslots && slots[last].valid) ? last : 0;
    for (int k = 0; k < nslots && !slots[sel].valid; k++) sel = (sel + 1) % nslots;

    bool autoboot = (last >= 0 && last < nslots && slots[last].valid);
    int64_t t0 = esp_timer_get_time();
    int shown_cd = -2;

    printf("\nMultiboot menu. Send 1-%d to start a firmware, l = list.\n", nslots);
    draw(sel, autoboot ? AUTOBOOT_S : -1, NULL);

    int64_t press_start = 0;
    bool pressed = false;
    bool long_fired = false;

    while (1) {
        bool now = gpio_get_level(PIN_BTN) == 0;
        int64_t t = esp_timer_get_time();

        if (now && !pressed) {
            pressed = true;
            long_fired = false;
            press_start = t;
            autoboot = false;
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
                    scan_slots();
                }
            }
        }

        if (autoboot) {
            int cd = AUTOBOOT_S - (int)((t - t0) / 1000000);
            if (cd <= 0) {
                boot_slot(last);
                autoboot = false;
            } else if (cd != shown_cd) {
                shown_cd = cd;
                draw(sel, cd, NULL);
            }
        }

        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
