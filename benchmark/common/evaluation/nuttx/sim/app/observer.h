#ifndef BENCH_SIM_OBSERVER_H
#define BENCH_SIM_OBSERVER_H
#include <nuttx/sensors/sensor.h>
struct sensor_lowerhalf_s *bench_sensor_find(int type, int devno);
struct sensor_lowerhalf_s *bench_sensor_find_custom(const char *path);
void bench_sensor_check_lower_disabled(struct sensor_lowerhalf_s *lower, size_t size);
void bench_sensor_check_disabled(int type, int devno, size_t size);
#endif
