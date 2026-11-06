/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "bmx280.h"
#include "bmx280_params.h"

int main(void)
{
    bmx280_t dev;
    int result = bmx280_init(&dev, &bmx280_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        printf("temp=%d pressure=%lu humidity=%u\n", bmx280_read_temperature(&dev), (unsigned long)bmx280_read_pressure(&dev), bme280_read_humidity(&dev));
    }
    return 0;
}
