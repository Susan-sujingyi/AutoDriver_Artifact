/* Passive observation of real Sensor registration. Linker wrapping delegates
 * every operation and result to the actual frozen OS; no fake node, error,
 * allocation or lock is substituted. It exposes the public lower-half ops
 * to lifecycle tests after real open/close has activated/deactivated them.
 */
#include <assert.h>
#include <string.h>
#include "observer.h"
#include "outcome.h"

static struct
{
  struct sensor_lowerhalf_s *lower;
  int devno;
  char path[128];
} registrations[64];

int __real_sensor_register(struct sensor_lowerhalf_s *lower, int devno);
void __real_sensor_unregister(struct sensor_lowerhalf_s *lower, int devno);
int __real_sensor_custom_register(struct sensor_lowerhalf_s *lower, const char *path, size_t size);
void __real_sensor_custom_unregister(struct sensor_lowerhalf_s *lower, const char *path);

static unsigned slot(struct sensor_lowerhalf_s *lower)
{
  for (unsigned i=0;i<64;i++) if (registrations[i].lower==lower) return i;
  for (unsigned i=0;i<64;i++) if (!registrations[i].lower)
    {memset(&registrations[i],0,sizeof(registrations[i]));registrations[i].lower=lower;registrations[i].devno=-1;return i;}
  bench_model_unsupported("sensor_observer_capacity");return 0;
}

int __wrap_sensor_custom_register(struct sensor_lowerhalf_s *lower,const char *path,size_t size)
{
  int result=__real_sensor_custom_register(lower,path,size);
  if(result==0)
    {
      unsigned i=slot(lower);
      if(strlen(path)>=sizeof(registrations[i].path))bench_model_unsupported("sensor_observer_path_length");
      strcpy(registrations[i].path,path);
    }
  return result;
}
void __wrap_sensor_custom_unregister(struct sensor_lowerhalf_s *lower,const char *path)
{
  __real_sensor_custom_unregister(lower,path);
  for(unsigned i=0;i<64;i++)if(registrations[i].lower==lower && !strcmp(registrations[i].path,path))registrations[i].lower=NULL;
}
struct sensor_lowerhalf_s *bench_sensor_find_custom(const char *path)
{
  for(unsigned i=0;i<64;i++)if(registrations[i].lower && !strcmp(registrations[i].path,path))return registrations[i].lower;
  return NULL;
}

int __wrap_sensor_register(struct sensor_lowerhalf_s *lower, int devno)
{
  int result = __real_sensor_register(lower, devno);
  if (result == 0)
    {
      unsigned i=slot(lower);
      registrations[i].devno = devno;
    }
  return result;
}

void __wrap_sensor_unregister(struct sensor_lowerhalf_s *lower, int devno)
{
  __real_sensor_unregister(lower, devno);
  for (unsigned i = 0; i < 64; i++)
    if (registrations[i].lower == lower && registrations[i].devno == devno)
      registrations[i].lower = NULL;
}

struct sensor_lowerhalf_s *bench_sensor_find(int type, int devno)
{
  for (unsigned i = 0; i < 64; i++)
    if (registrations[i].lower && registrations[i].lower->type == type &&
        registrations[i].devno == devno)
      return registrations[i].lower;
  return NULL;
}

void bench_sensor_check_disabled(int type, int devno, size_t size)
{
  bench_sensor_check_lower_disabled(bench_sensor_find(type,devno),size);
}

void bench_sensor_check_lower_disabled(struct sensor_lowerhalf_s *lower, size_t size)
{
  char buffer[256], before[256];
  assert(lower && lower->ops && lower->ops->fetch && size <= sizeof(buffer));
  memset(buffer, 0xa5, sizeof(buffer));
  memcpy(before, buffer, sizeof(buffer));
  assert(lower->ops->fetch(lower, NULL, buffer, size) < 0);
  assert(!memcmp(buffer, before, sizeof(buffer)));
}
