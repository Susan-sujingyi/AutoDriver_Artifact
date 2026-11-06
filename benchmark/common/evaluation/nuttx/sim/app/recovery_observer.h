/* Host-only observation; successful events still need device validation. */
#ifndef BENCH_RECOVERY_OBSERVER_H
#define BENCH_RECOVERY_OBSERVER_H
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static ssize_t bench_observe_read(int fd, void *event, size_t size,
                                  size_t capacity, bool permit_failure)
{
  assert(size > 0 && size <= capacity);
  unsigned char before[capacity];
  memcpy(before, event, capacity);
  ssize_t result = read(fd, event, size);
  assert(!memcmp((unsigned char *)event + size, before + size, capacity - size));
  if (permit_failure && result < 0)
    {
      assert(!memcmp(event, before, capacity));
      printf("BENCHSIM transient_read outcome=propagated_error result=%ld\n", (long)result);
      return result;
    }
  assert(result == (ssize_t)size);
  if (permit_failure)
    printf("BENCHSIM transient_read outcome=complete_event_requires_validation\n");
  return result;
}
static void bench_finish_transient(int *pending_error)
{
  /* A valid cached event can avoid the armed transfer. Do not claim the
   * transfer occurred; clear the test injection before subsequent requests.
   * The successful-event validator is still mandatory on that path. */
  printf("BENCHSIM transient_fault consumed=%u\n", *pending_error == 0);
  *pending_error = 0;
}
#endif
