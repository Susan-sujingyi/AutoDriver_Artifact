/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "apds99xx.h"
#include "apds99xx_params.h"

int main(void)
{
    apds99xx_t dev;
    int result = apds99xx_init(&dev, &apds99xx_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        uint16_t value = 0; int status = apds99xx_data_ready_prx(&dev);
        if (status == 0) { status = apds99xx_read_prx_raw(&dev, &value); } printf("status=%d proximity=%u\n", status, value);
    }
    return 0;
}
