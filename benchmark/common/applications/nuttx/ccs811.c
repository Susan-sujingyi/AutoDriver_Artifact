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
  const char *paths[] = {"/dev/uorb/sensor_co20",
                        "/dev/uorb/sensor_tvoc0"};
  const char *channels[] = {"co2", "tvoc"};
  const char *units[] = {"ppm_eCO2", "ppm_TVOC"};
  int fd[2] = {-1, -1};
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
  for (channel = 0; channel < 2; channel++)
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
      for (channel = 0; channel < 2; channel++)
        {
          struct sensor_co2 co2;
          struct sensor_tvoc tvoc;
          void *event = channel == 0 ? (void *)&co2 :
                        (void *)&tvoc;
          size_t size = channel == 0 ? sizeof(co2) :
                        sizeof(tvoc);
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

          value = channel == 0 ? co2.co2 :
                  tvoc.tvoc;
          timestamp = channel == 0 ? co2.timestamp :
                      tvoc.timestamp;
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
  for (channel = 0; channel < 2; channel++)
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
