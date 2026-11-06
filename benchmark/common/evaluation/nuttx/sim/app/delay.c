/* Architecture delay hooks for the simulated clock peripheral. The frozen
 * OS blocking sleeps still use its scheduler/watchdogs. The generic alarm
 * busy-wait would spin on a clock that only advances at virtual events.
 */
#include <nuttx/arch.h>
#include <stdint.h>

extern void host_sleep(uint64_t nanoseconds);

void up_ndelay(unsigned long nanoseconds)
{
  host_sleep(nanoseconds);
}

void up_udelay(useconds_t microseconds)
{
  host_sleep((uint64_t)microseconds * 1000);
}
