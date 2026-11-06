/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "bh1750fvi.h"
#include "bh1750fvi_params.h"

int main(void)
{
    bh1750fvi_t dev;
    int result = bh1750fvi_init(&dev, &bh1750fvi_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        printf("lux=%u\n", (unsigned)bh1750fvi_sample(&dev));
    }
    return 0;
}
