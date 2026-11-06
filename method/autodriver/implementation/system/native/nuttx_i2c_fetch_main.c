/* Method-owned public NuttX New Sensor mock. No device protocol lives here. */
#include <stdio.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <float.h>
#include <math.h>
#include <time.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/i2c/i2c_master.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/@MODULE@.h>
_Static_assert(sizeof(sensor_data_t)==4 && FLT_RADIX==2 && FLT_MANT_DIG==24 && FLT_MAX_EXP==128,"mock requires public binary32 ABI");
static uint64_t now_us;
static unsigned operation,fetch_id;
struct response {const uint8_t *data;size_t size;};
static const struct response *responses;
static size_t response_count,response_index;
static int fail_next,registration_fail_at,registration_calls,interrupt_sleep;
static struct sensor_lowerhalf_s *nodes[128];
static void *allocations[256];static unsigned allocation_count;
static void event(const char *phase,const char *format,...) {
 printf("{\"phase\":\"%s\",\"tick\":%llu,\"frequency\":1000000",phase,(unsigned long long)now_us);
 if(format && *format){printf(",");va_list args;va_start(args,format);vprintf(format,args);va_end(args);}puts("}");
}
void *kmm_malloc(size_t size){void *p=malloc(size);if(p){if(allocation_count==256)abort();allocations[allocation_count++]=p;}return p;}
void *kmm_zalloc(size_t size){void *p=kmm_malloc(size);if(p)memset(p,0,size);return p;}
void *kmm_calloc(size_t n,size_t size){if(size&&n>SIZE_MAX/size)return NULL;return kmm_zalloc(n*size);}
void kmm_free(void *p){if(!p)return;for(unsigned i=0;i<allocation_count;i++)if(allocations[i]==p){allocations[i]=allocations[--allocation_count];free(p);return;}abort();}
int nxsig_usleep(useconds_t delay){if(interrupt_sleep){interrupt_sleep=0;now_us+=delay/2;event("sleep_interrupted","\"requested_us\":%u,\"elapsed_us\":%u",(unsigned)delay,(unsigned)(delay/2));return -EINTR;}now_us+=delay;event("sleep_end","\"delay_us\":%u",(unsigned)delay);return 0;}
int clock_gettime(clockid_t id,struct timespec *ts){(void)id;ts->tv_sec=now_us/1000000;ts->tv_nsec=(now_us%1000000)*1000;return 0;}
clock_t clock_systime_ticks(void){return now_us/1000;}
void clock_systime_timespec(struct timespec *ts){clock_gettime(CLOCK_MONOTONIC,ts);}
void up_udelay(unsigned int delay){now_us+=delay;event("busy_wait_end","\"delay_us\":%u",delay);}
void up_mdelay(unsigned int delay){up_udelay(delay*1000);}
int nxmutex_init(mutex_t *m){m->locked=false;return 0;}
int nxmutex_destroy(mutex_t *m){return m->locked?-EBUSY:0;}
int nxmutex_lock(mutex_t *m){if(m->locked)return -EBUSY;m->locked=true;event("mutex_lock","");return 0;}
int nxmutex_trylock(mutex_t *m){return nxmutex_lock(m);}
int nxmutex_unlock(mutex_t *m){if(!m->locked)return -EPERM;m->locked=false;event("mutex_unlock","");return 0;}
static void notification(void *p){(void)p;event("notification","");}
int sensor_register(struct sensor_lowerhalf_s *dev,int devno){
 registration_calls++;event("registration","\"type\":%d,\"devno\":%d",dev->type,devno);
 if(registration_calls==registration_fail_at)return -ENOMEM;
 if(dev->type<0 || dev->type>=SENSOR_TYPE_COUNT){event("registration_rejected","\"reason\":\"invalid_type\",\"type\":%d",dev->type);return -EINVAL;}
 if(dev->type>=128){event("executor_gap","\"reason\":\"native registry observation bound\"");return -ENOTSUP;}
 if(nodes[dev->type]){event("registration_rejected","\"reason\":\"duplicate_type\",\"type\":%d",dev->type);return -EEXIST;}
 nodes[dev->type]=dev;dev->priv=dev;dev->notify_event=notification;return 0;
}
void sensor_unregister(struct sensor_lowerhalf_s *dev,int devno){(void)devno;if(dev->type>=0&&dev->type<128&&nodes[dev->type]==dev)nodes[dev->type]=NULL;event("unregistration","\"type\":%d",dev->type);}
static int transfer(struct i2c_master_s *bus,struct i2c_msg_s *msgs,int count){
 (void)bus;if(count<1 || count>16){event("executor_gap","\"reason\":\"I2C segment bound\"");return -EINVAL;}
 unsigned id=++operation;size_t write_len=0,read_len=0;bool seen_read=false;
 for(int j=0;j<count;j++){
  struct i2c_msg_s *m=&msgs[j];bool read=(m->flags&I2C_M_READ)!=0;
  if(m->length<0 || (size_t)m->length>65536 || (m->length&&!m->buffer))return -EINVAL;
  if(m->addr!=msgs[0].addr || m->frequency!=msgs[0].frequency || (!read&&seen_read)){
   event("executor_gap","\"reason\":\"mixed address/rate or write after read in one native transaction\"");return -ENOTSUP;
  }
  if(read){read_len+=m->length;seen_read=true;}else write_len+=m->length;
 }
 if(read_len>65536 || write_len>65536){event("executor_gap","\"reason\":\"I2C byte bound\"");return -EINVAL;}
 printf("{\"phase\":\"submit\",\"tick\":%llu,\"frequency\":1000000,\"operation\":%u,\"address\":%u,\"bus_hz\":%u,\"write_len\":%zu,\"read_len\":%zu,\"write\":[",(unsigned long long)now_us,id,msgs[0].addr,msgs[0].frequency,write_len,read_len);
 size_t emitted=0;for(int j=0;j<count;j++)if(!(msgs[j].flags&I2C_M_READ))for(ssize_t k=0;k<msgs[j].length;k++)printf("%s%u",emitted++?",":"",msgs[j].buffer[k]);puts("]}");
 for(int j=0;j<count;j++)event("i2c_segment","\"operation\":%u,\"index\":%d,\"flags\":%u,\"length\":%ld",id,j,(unsigned)msgs[j].flags,(long)msgs[j].length);
 event("physical_start","\"operation\":%u",id);now_us+=100;
 if(fail_next){fail_next=0;event("physical_failure","\"operation\":%u",id);event("return_failure","\"operation\":%u",id);return -EIO;}
 if(@FRAMEWORK_PROBE@){
  if(read_len){event("physical_failure","\"operation\":%u",id);event("return_failure","\"operation\":%u",id);return -EIO;}
  event("physical_success","\"operation\":%u",id);event("return_success","\"operation\":%u,\"data\":[]",id);return 0;
 }
 if(response_index>=response_count){event("executor_gap","\"reason\":\"native transaction exceeds the bounded graph-derived response script\"");event("physical_failure","\"operation\":%u",id);event("return_failure","\"operation\":%u",id);return -EIO;}
 const struct response *response=&responses[response_index++];
 if(read_len!=response->size){event("executor_gap","\"reason\":\"native read length differs from graph-derived response script\"");event("physical_failure","\"operation\":%u",id);event("return_failure","\"operation\":%u",id);return -EIO;}
 size_t offset=0;for(int j=0;j<count;j++)if(msgs[j].flags&I2C_M_READ){if(msgs[j].length)memcpy(msgs[j].buffer,response->data+offset,msgs[j].length);offset+=msgs[j].length;}
 event("physical_success","\"operation\":%u",id);
 printf("{\"phase\":\"return_success\",\"tick\":%llu,\"frequency\":1000000,\"operation\":%u,\"data\":[",(unsigned long long)now_us,id);
 for(size_t k=0;k<response->size;k++)printf("%s%u",k?",":"",response->data[k]);puts("]}");
 return 0;
}
static const struct i2c_ops_s bus_ops={.transfer=transfer};static struct i2c_master_s bus={.ops=&bus_ops};
struct input_case {const char *name,*channel;const struct response *initial;size_t initial_count;const struct response *acquire;size_t acquire_count;int fail;int reg_fail;int probe;int repeat;};
static const struct input_case cases[]={@CASES@};
struct channel {const char *name,*structure;int type;size_t size,value_offset,timestamp_offset;};
static const struct channel channels[]={@CHANNELS@};
static const struct channel *find_channel(const char *name){for(size_t i=0;i<sizeof(channels)/sizeof(*channels);i++)if(!strcmp(name,channels[i].name))return &channels[i];return NULL;}
static void result_output(const struct channel *channel,const void *buffer){
 float value;uint64_t timestamp;memcpy(&value,(const char*)buffer+channel->value_offset,sizeof(value));memcpy(&timestamp,(const char*)buffer+channel->timestamp_offset,sizeof(timestamp));
 uint32_t bits;memcpy(&bits,&value,sizeof(bits));
 char text[64];if(isfinite(value))snprintf(text,sizeof(text),"%.9g",(double)value);else strcpy(text,"null");
 event("output","\"channel\":\"%s\",\"native_structure\":\"%s\",\"value\":%s,\"value_bits\":%u,\"storage_type\":\"f32\",\"timestamp\":%llu,\"failed\":false",channel->name,channel->structure,text,bits,(unsigned long long)timestamp);
}
int main(int argc,char **argv){
 setbuf(stdout,NULL);
 if(argc!=2)return 64;size_t selected=strtoul(argv[1],NULL,10);if(selected>=sizeof(cases)/sizeof(*cases))return 64;
 for(size_t i=selected;i==selected;i++){
  const struct input_case *c=&cases[i];memset(nodes,0,sizeof(nodes));now_us=0;operation=0;fetch_id=0;registration_calls=0;allocation_count=0;
  responses=c->initial;response_count=c->initial_count;response_index=0;fail_next=0;interrupt_sleep=0;registration_fail_at=c->reg_fail;
  printf("{\"phase\":\"case_begin\",\"case\":\"%s\"}\n",c->name);
  int status=@REGISTER@(&bus,@ADDRESS@,0);event("native_return","\"request\":\"register\",\"result\":%d",status);
  if(status<0){unsigned active=0;for(size_t j=0;j<128;j++)if(nodes[j])active++;event("registration_failure_state","\"active_nodes\":%u,\"allocations\":%u",active,allocation_count);goto done;}
  const struct channel *channel=find_channel(c->channel);if(!channel || channel->type<0 || channel->type>=128 || channel->size>128)return 65;
  struct sensor_lowerhalf_s *node=nodes[channel->type];
  if(!node || !node->ops || !node->ops->activate || !node->ops->fetch){event("native_requirement_failure","\"reason\":\"required public native node/activate/fetch absent\"");goto done;}
  if(node->ops->open){status=node->ops->open(node,NULL);event("native_return","\"request\":\"open\",\"result\":%d",status);if(status<0)goto done;}
  status=node->ops->activate(node,NULL,true);event("native_return","\"request\":\"activate\",\"result\":%d",status);
  if(c->probe==1){status=node->ops->activate(node,NULL,false);event("native_return","\"request\":\"disable\",\"result\":%d",status);}
  if(c->probe==4){
   struct sensor_lowerhalf_s *other=NULL;for(size_t j=0;j<sizeof(channels)/sizeof(*channels);j++)if(channels[j].type!=channel->type){other=nodes[channels[j].type];break;}
   if(!other || !other->ops || !other->ops->activate){event("executor_gap","\"reason\":\"second native node/activation absent\"");goto done;}
   if(other->ops->open)other->ops->open(other,NULL);
   other->ops->activate(other,NULL,true);other->ops->activate(other,NULL,false);
   if(other->ops->close)other->ops->close(other,NULL);
   event("other_node_disabled","");
  }
  for(unsigned repeat=0;repeat<(unsigned)c->repeat;repeat++){
   union {max_align_t alignment;uint8_t bytes[128];} output;memset(&output,0xa5,sizeof(output));
   size_t size=channel->size;fetch_id++;
   responses=c->acquire;response_count=c->acquire_count;response_index=0;
   event("request_boundary","\"request\":\"fetch\",\"channel\":\"%s\",\"instance\":\"fetch%u\"",c->channel,fetch_id);
   fail_next=c->fail;interrupt_sleep=c->probe==5;uint64_t start=now_us;status=node->ops->fetch(node,NULL,c->probe==2?NULL:(char*)&output,c->probe==3?size-1:size);
   event("native_return","\"request\":\"fetch\",\"result\":%d,\"bytes\":%zu,\"elapsed_us\":%llu,\"probe_mode\":%d",status,size,(unsigned long long)(now_us-start),c->probe);
   if(status>0)result_output(channel,&output);
   else{bool changed=false;for(size_t j=0;j<size;j++)if(output.bytes[j]!=0xa5)changed=true;event("failed_fetch_buffer","\"changed\":%s",changed?"true":"false");}
   if(c->fail)break;
  }
  node->ops->activate(node,NULL,false);
  if(node->ops->close){status=node->ops->close(node,NULL);event("native_return","\"request\":\"close\",\"result\":%d",status);}
 done:
  puts("{\"phase\":\"case_end\"}");
 }
 return 0;
}
