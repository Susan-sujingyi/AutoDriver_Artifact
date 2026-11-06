/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "ccs811.h"
#include "ccs811_params.h"

int main(void)
{
    ccs811_t dev;
    int result = ccs811_init(&dev, &ccs811_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        uint16_t tvoc = 0, eco2 = 0; int status = ccs811_read_iaq(&dev, &tvoc, &eco2, NULL, NULL); printf("status=%d tvoc=%u eco2=%u\n", status, tvoc, eco2);
    }
    return 0;
}
