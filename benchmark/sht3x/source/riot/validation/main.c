/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "sht3x.h"
#include "sht3x_params.h"

int main(void)
{
    sht3x_dev_t dev;
    int result = sht3x_init(&dev, &sht3x_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        int16_t t = 0, h = 0; int status = sht3x_read(&dev, &t, &h); printf("status=%d temp=%d humidity=%d\n", status, t, h);
    }
    return 0;
}
