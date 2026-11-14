idf_component_register(SRCS "hooks.c"
                       INCLUDE_DIRS "."
                       PRIV_INCLUDE_DIRS "$ENV{IDF_PATH}/components/bootloader_support/include_bootloader" "$ENV{IDF_PATH}/components/spi_flash/include"
                       REQUIRES bootloader_support hal soc esp_rom log)
