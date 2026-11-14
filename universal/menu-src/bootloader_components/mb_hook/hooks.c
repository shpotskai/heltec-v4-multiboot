/*
 * Multiboot bootloader hook for Heltec WiFi LoRa 32 V4.
 *
 * After a power-on / RST-button reset the white LED (GPIO35) lights up for
 * MB_WINDOW_MS. If the PRG button (GPIO0) is held during that window, the
 * otadata partition is erased, so the bootloader starts the factory app
 * (the multiboot menu) instead of the last selected firmware.
 *
 * Wake-ups from deep sleep and software resets are not delayed.
 */
#include <stdint.h>
#include <stdbool.h>
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_rom_gpio.h"
#include "soc/gpio_struct.h"
#include "soc/reset_reasons.h"
#include "hal/gpio_ll.h"
#include "bootloader_flash_priv.h"

#define MB_BTN_GPIO        0
#define MB_LED_GPIO        35
#define MB_WINDOW_MS       1500
#define MB_HOLD_MS         150
#define MB_OTADATA_OFFSET  0xE000
#define MB_OTADATA_SIZE    0x2000

static const char *TAG = "multiboot";

/* Forces the linker to keep this file (hooks are weak symbols). */
void bootloader_hooks_include(void)
{
}

void bootloader_before_init(void)
{
}

static inline bool btn_pressed(void)
{
    return gpio_ll_get_level(&GPIO, MB_BTN_GPIO) == 0;
}

static void led(bool on)
{
    gpio_ll_set_level(&GPIO, MB_LED_GPIO, on ? 1 : 0);
}

void bootloader_after_init(void)
{
    soc_reset_reason_t rr = esp_rom_get_reset_reason(0);
    if (rr != RESET_REASON_CHIP_POWER_ON) {
        return; /* deep-sleep wake, watchdog, software reset: boot immediately */
    }

    esp_rom_gpio_pad_select_gpio(MB_BTN_GPIO);
    gpio_ll_input_enable(&GPIO, MB_BTN_GPIO);
    gpio_ll_pullup_en(&GPIO, MB_BTN_GPIO);

    esp_rom_gpio_pad_select_gpio(MB_LED_GPIO);
    gpio_ll_output_enable(&GPIO, MB_LED_GPIO);
    led(true);

    uint32_t held_ms = 0;
    bool enter_menu = false;
    for (uint32_t t = 0; t < MB_WINDOW_MS; t += 10) {
        esp_rom_delay_us(10000);
        if (btn_pressed()) {
            held_ms += 10;
            if (held_ms >= MB_HOLD_MS) {
                enter_menu = true;
                break;
            }
        } else {
            held_ms = 0;
        }
    }

    if (enter_menu) {
        ESP_LOGI(TAG, "PRG held: opening multiboot menu");
        if (bootloader_flash_erase_range(MB_OTADATA_OFFSET, MB_OTADATA_SIZE) != ESP_OK) {
            ESP_LOGE(TAG, "otadata erase failed");
        }
        /* blink to confirm */
        for (int i = 0; i < 3; i++) {
            led(false); esp_rom_delay_us(80000);
            led(true);  esp_rom_delay_us(80000);
        }
    }

    led(false);
    gpio_ll_output_disable(&GPIO, MB_LED_GPIO);
}
