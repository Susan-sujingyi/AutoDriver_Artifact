/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "lsm303dlhc.h"
#include "lsm303dlhc_params.h"

int main(void)
{
    lsm303dlhc_t dev;
    int result = lsm303dlhc_init(&dev, &lsm303dlhc_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        lsm303dlhc_3d_data_t data = {0}; int status = lsm303dlhc_read_mag(&dev, &data); printf("status=%d xyz=%d,%d,%d\n", status, data.x_axis, data.y_axis, data.z_axis);
    }
    return 0;
}
