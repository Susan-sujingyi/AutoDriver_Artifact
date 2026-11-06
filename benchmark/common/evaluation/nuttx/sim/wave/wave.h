#ifndef BENCH_SIM_WAVE_H
#define BENCH_SIM_WAVE_H
#include <stdbool.h>
#include <stdint.h>
struct bench_wave_ops
{
  int (*drive)(void *context, bool low);
  int (*begin)(void *context);
  bool (*level)(void *context, uint32_t elapsed_us);
  void (*end)(void *context);
};
void bench_wave_install(const struct bench_wave_ops *ops, void *context);
#endif
