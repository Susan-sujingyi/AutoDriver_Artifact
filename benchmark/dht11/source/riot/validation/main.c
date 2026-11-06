/* Source admission API caller; not an agent input or a behavior oracle. */
#include <stdio.h>
#include "ztimer.h"
#include "dht.h"
#include "dht_params.h"

int main(void)
{
    dht_t dev;
    int result = dht_init(&dev, &dht_params[0]);
    if (result != 0) { printf("init=%d\n", result); return 1; }
    for (unsigned i = 0; i < 3; ++i) {
        ztimer_sleep(ZTIMER_MSEC, 3000);
        int16_t t = 0, h = 0; int status = dht_read(&dev, &t, &h); printf("status=%d temp=%d humidity=%d\n", status, t, h);
    }
    return 0;
}
