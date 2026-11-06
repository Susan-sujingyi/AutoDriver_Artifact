/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "lpsxxx.h"
#include "lpsxxx_params.h"

int main(void)
{
    lpsxxx_t dev;
    int result = lpsxxx_init(&dev, &lpsxxx_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        int16_t temp = 0; uint16_t pressure = 0; int t = lpsxxx_read_temp(&dev, &temp); int p = lpsxxx_read_pres(&dev, &pressure); printf("status=%d,%d temp=%d pressure=%u\n", t, p, temp, pressure);
    }
    return 0;
}
