/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "adt7310.h"
#include "periph/spi.h"
#include "periph/gpio.h"

int main(void)
{
    adt7310_t dev;
    int result = adt7310_init(&dev, SPI_DEV(0), SPI_CLK_1MHZ, GPIO_PIN(1, 4));
    if (result != 0) { printf("init=%d\n", result); return 1; }
    if (adt7310_set_config(&dev, ADT7310_MODE_CONTINUOUS | ADT7310_CONF_RESOLUTION(1)) != 0) return 2;
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        printf("temperature=%ld\n", (long)adt7310_read(&dev));
    }
    return 0;
}
