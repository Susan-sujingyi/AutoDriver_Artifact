/* Public native-event acceptance client. No device protocol or compensation. */
#include <nuttx/config.h>
#include <nuttx/sensors/sensor.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "run_identity.h"

int main(int argc, char **argv)
{
  const char *paths[] = {"/dev/uorb/sensor_ambient_temp0",
                        "/dev/uorb/sensor_humi0", "/dev/uorb/sensor_baro0"};
  const char *channels[] = {"temperature", "humidity", "pressure"};
  const char *units[] = {"degC", "percent_RH", "hPa"};
  int fd[3] = {-1, -1, -1};
  char *end;
  long count = 10;
  int result = 0;
  int channel;
  long i;

  if (argc > 2)
    {
      fprintf(stderr, "usage: sensor_sample [sample-count:1..1000]\n");
      return 2;
    }

  if (argc == 2)
    {
      errno = 0;
      count = strtol(argv[1], &end, 10);
      if (errno || end == argv[1] || *end || count < 1 || count > 1000)
        {
          return 2;
        }
    }

  printf("{\"event\":\"identity\",\"task\":\"%s\",\"build_id\":\"%s\","
         "\"interface\":\"new_sensor_fetch/v1\",\"expected_samples\":%ld}\n",
         SENSOR_SAMPLE_TASK_ID, SENSOR_SAMPLE_BUILD_ID, count);
  for (channel = 0; channel < 3; channel++)
    {
      fd[channel] = open(paths[channel], O_RDONLY);
      if (fd[channel] < 0)
        {
          printf("{\"event\":\"open_error\",\"channel\":\"%s\",\"errno\":%d}\n",
                 channels[channel], errno);
          result = 1;
          goto cleanup;
        }
    }

  for (i = 0; i < count; i++)
    {
      for (channel = 0; channel < 3; channel++)
        {
          struct sensor_temp temperature;
          struct sensor_humi humidity;
          struct sensor_baro pressure;
          void *event = channel == 0 ? (void *)&temperature :
                        channel == 1 ? (void *)&humidity : (void *)&pressure;
          size_t size = channel == 0 ? sizeof(temperature) :
                        channel == 1 ? sizeof(humidity) : sizeof(pressure);
          ssize_t length = read(fd[channel], event, size);
          float value;
          uint64_t timestamp;
          if (length != size)
            {
              printf("{\"event\":\"read_error\",\"sample\":%ld,\"channel\":\"%s\","
                     "\"bytes\":%ld,\"errno\":%d}\n", i, channels[channel],
                     (long)length, length < 0 ? errno : 0);
              result = 1;
              continue;
            }

          value = channel == 0 ? temperature.temperature :
                  channel == 1 ? humidity.humidity : pressure.pressure;
          timestamp = channel == 0 ? temperature.timestamp :
                      channel == 1 ? humidity.timestamp : pressure.timestamp;
          if (!isfinite(value))
            {
              printf("{\"event\":\"invalid_sample\",\"sample\":%ld,"
                     "\"channel\":\"%s\"}\n", i, channels[channel]);
              result = 1;
              continue;
            }

          printf("{\"event\":\"sample\",\"sample\":%ld,\"channel\":\"%s\","
                 "\"timestamp_us\":%" PRIu64 ",\"value\":%.6f,\"unit\":\"%s\"}\n",
                 i, channels[channel], timestamp, (double)value, units[channel]);
        }

      if (i + 1 < count)
        {
          sleep(1);
        }
    }

cleanup:
  for (channel = 0; channel < 3; channel++)
    {
      if (fd[channel] >= 0 && close(fd[channel]) < 0)
        {
          printf("{\"event\":\"close_error\",\"channel\":\"%s\",\"errno\":%d}\n",
                 channels[channel], errno);
          result = 1;
        }
    }

  printf("{\"event\":\"end\",\"client_status\":%d,\"attempted_samples\":%ld}\n", result, count);
  return result;
}
