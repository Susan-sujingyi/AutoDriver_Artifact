/* Public acceptance client: native event I/O only, no sensor protocol. */
#include <nuttx/config.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/lsm6dso.h>
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
  struct sensor_accel sample;
  char *end;
  long count = 10;
  int fd;
  int result = 0;
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
  fd = open("/dev/uorb/sensor_accel0", O_RDONLY);
  if (fd < 0)
    {
      printf("{\"event\":\"open_error\",\"errno\":%d}\n", errno);
      return 1;
    }

  for (i = 0; i < count; i++)
    {
      ssize_t length = read(fd, &sample, sizeof(sample));
      if (length != sizeof(sample))
        {
          printf("{\"event\":\"read_error\",\"sample\":%ld,\"bytes\":%ld,"
                 "\"errno\":%d}\n", i, (long)length, length < 0 ? errno : 0);
          result = 1;
        }
      else
        {
          printf("{\"event\":\"sample\",\"sample\":%ld,\"timestamp_us\":%" PRIu64
                 ",\"acceleration_m_s2\":[%.9g,%.9g,%.9g]}\n", i, sample.timestamp, (double)sample.x, (double)sample.y, (double)sample.z);
        }

      if (i + 1 < count)
        {
          sleep(1);
        }
    }

  if (close(fd) < 0)
    {
      printf("{\"event\":\"close_error\",\"errno\":%d}\n", errno);
      result = 1;
    }

  printf("{\"event\":\"end\",\"client_status\":%d,\"attempted_samples\":%ld}\n", result, count);
  return result;
}
