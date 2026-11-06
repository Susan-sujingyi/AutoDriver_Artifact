/* Product configuration only; source driver is unchanged. */
#pragma once
#define BMX280_PARAMS { .spi = SPI_DEV(0), .clk = SPI_CLK_1MHZ, .cs = GPIO_PIN(1, 4), .t_sb = BMX280_SB_0_5, .filter = BMX280_FILTER_OFF, .run_mode = BMX280_MODE_FORCED, .temp_oversample = BMX280_OSRS_X1, .press_oversample = BMX280_OSRS_X16, .humid_oversample = BMX280_OSRS_X1 }
