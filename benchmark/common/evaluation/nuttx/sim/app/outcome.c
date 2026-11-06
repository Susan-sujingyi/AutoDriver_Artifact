#include <nuttx/syslog/syslog.h>
#include <syslog.h>
#include "outcome.h"

extern void host_abort(int status) __attribute__((noreturn));

void bench_model_unsupported(const char *capability)
{
  syslog(LOG_ERR, "BENCHSIM model_unsupported capability=%s\n", capability);
  syslog_flush();
  host_abort(87);
}
