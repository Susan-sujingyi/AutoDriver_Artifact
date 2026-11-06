/* Trusted entry point. Each host invocation starts a fresh real NuttX OS. */
#include <nuttx/config.h>
#include <sys/boardctl.h>
#include <stdio.h>

extern int g_argc;
extern char **g_argv;
int bench_case_main(int argc, char **argv);
void bench_install_crash_handler(void);

int bench_main(int argc, char **argv)
{
  bench_install_crash_handler();
  int status = bench_case_main(g_argc, g_argv);
  printf("BENCHSIM {\"event\":\"end\",\"status\":%d}\n", status);
  fflush(stdout);
  boardctl(BOARDIOC_POWEROFF, status);
  return status;
}
