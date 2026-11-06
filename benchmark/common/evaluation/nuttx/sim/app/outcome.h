/* Explicit model capability limits are not candidate protocol failures. */
#ifndef BENCH_SIM_OUTCOME_H
#define BENCH_SIM_OUTCOME_H
void bench_model_unsupported(const char *capability) __attribute__((noreturn));
#endif
