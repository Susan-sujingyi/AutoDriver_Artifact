/* Deterministic clock source for the frozen NuttX SIM architecture.
 * This replaces only the host clock peripheral in a disposable build tree.
 * The real SIM oneshot driver, OS watchdogs and scheduler deliver wakeups.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <signal.h>
#include "sim_internal.h"

#ifndef CONFIG_SIM_WALLTIME_SIGNAL
#error "The deterministic timer must use the real SIM interrupt delivery path"
#endif
#ifdef CONFIG_SMP
#error "The deterministic clock currently supports single-core SIM only"
#endif

static uint64_t simulated_ns;
static uint64_t timer_deadline;
static bool timer_armed;

int host_inittimer(void)
{
  simulated_ns = 0;
  timer_armed = false;
  return 0;
}

uint64_t host_gettime(bool rtc)
{
  /* A fixed epoch keeps wall timestamps reproducible as well. */
  return simulated_ns + (rtc ? 946684800000000000ull : 0);
}

void host_sleep(uint64_t nsec)
{
  if (nsec > UINT64_MAX - simulated_ns)
    {
      abort();
    }

  host_sleepuntil(simulated_ns + nsec);
}

void host_sleepuntil(uint64_t nsec)
{
  /* Deliver at each programmed virtual hardware deadline, not after a whole
   * busy delay. SIGALRM goes through frozen SIM's real IRQ/context switching
   * code. POSIX masking retains a pending IRQ during NuttX critical sections;
   * unmasking delivers it through the same path as the native SIM timer.
   * No host wall-clock timer is installed and no kernel service is replaced.
   */
  while (timer_armed && timer_deadline <= nsec)
    {
      uint64_t due = timer_deadline;
      timer_armed = false;
      if (due > simulated_ns) simulated_ns = due;
      if (raise(SIGALRM) != 0) abort();
      /* A preempting task can advance time beyond this caller's deadline. */
      if (simulated_ns >= nsec) return;
    }
  if (nsec > simulated_ns) simulated_ns = nsec;
}

void host_set_timeratio(int ratio)
{
  /* There is no wall-clock ratio in this backend. */
  if (ratio != 100)
    {
      abort();
    }
}

int host_settimer(uint64_t nsec)
{
  timer_deadline = nsec;
  timer_armed = nsec != UINT64_MAX;
  return 0;
}

int host_timerirq(void)
{
  return SIGALRM;
}
