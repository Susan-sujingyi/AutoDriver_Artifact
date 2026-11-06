/* A failed assertion must terminate its case, not leave the SIM idle task
 * running until a host timeout. Use real OS panic notifications for task
 * assertions and the board hook for early fatal panics. Preserve the exact
 * location/expression; the supervisor determines model vs candidate blame.
 */
#include <nuttx/config.h>
#include <nuttx/board.h>
#include <nuttx/panic_notifier.h>
#include <nuttx/syslog/syslog.h>
#include <syslog.h>

extern void host_abort(int status);

static int on_panic(struct notifier_block *block, unsigned long action,
                     void *data)
{
  struct panic_notifier_s *panic = data;
  syslog(LOG_ERR, "BENCHSIM assertion action=%lu file=%s line=%d expression=%s\n",
         action, panic->filename ? panic->filename : "?", panic->linenum,
         panic->msg ? panic->msg : "?");
  syslog_flush();
  host_abort(86);
  return 0;
}

static struct notifier_block notifier = {.notifier_call = on_panic};

void bench_install_crash_handler(void)
{
  panic_notifier_chain_register(&notifier);
}

void board_crashdump(uintptr_t sp, struct tcb_s *tcb, const char *filename,
                     int lineno, const char *message, void *registers)
{
  host_abort(86);
}
