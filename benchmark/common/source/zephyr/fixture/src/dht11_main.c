/* SPDX-License-Identifier: Apache-2.0 */
/* Host-only deterministic wire/clock instrumentation. The selected original
 * driver, GPIO API, Kconfig, DT initialization and final firmware link stay real.
 * No synthetic source result substitutes for executing sample_fetch/channel_get. */
#include <zephyr/kernel.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/gpio/gpio_emul.h>
#include <posix_board_if.h>
#include <stdint.h>
#include <errno.h>
#define NODE DT_NODELABEL(source_sensor)
static const struct gpio_dt_spec wire = GPIO_DT_SPEC_GET(NODE, dio_gpios);
static bool simulated, started, invalid;
static uint64_t us, origin;
static uint32_t start_width, clock_reads;
static uint8_t payload[5];
uint32_t __real_sys_clock_cycle_get_32(void);
void __real_arch_busy_wait(uint32_t duration);
static int level(uint64_t delta) {
    if (delta < 30) return 1;
    delta -= 30;
    if (delta < 80) return 0;
    delta -= 80;
    if (delta < 80) return 1;
    delta -= 80;
    for (int n=0;n<40;n++) {
        if (delta < 50) return 0;
        delta -= 50;
        uint64_t high = (payload[n/8] & (1u << (7-n%8))) ? 70 : 27;
        if (delta < high) return 1;
        delta -= high;
    }
    return delta >= 50;
}
uint32_t __wrap_sys_clock_cycle_get_32(void) {
    if (!simulated) return __real_sys_clock_cycle_get_32();
    us++;clock_reads++;
    if (started) gpio_emul_input_set_dt(&wire, level(us-origin));
    return (uint32_t)(us * sys_clock_hw_cycles_per_sec() / 1000000);
}
void __wrap_arch_busy_wait(uint32_t duration) {
    if (!simulated) {__real_arch_busy_wait(duration);return;}
    if (gpio_emul_output_get_dt(&wire)!=0 || duration<18000) invalid=true;
    us+=duration;origin=us;start_width=duration;started=true;
    gpio_emul_input_set_dt(&wire,1);
}
int main(void) {
    const struct device *dev=DEVICE_DT_GET(NODE);
    if (!device_is_ready(dev)) {printk("SOURCE_BUILD_SMOKE_FAIL init\n");posix_exit(1);}
    const uint8_t vectors[][2]={{24,53},{0,30},{50,80},{25,90}};
    for (unsigned i=0;i<4;i++) {
        payload[0]=vectors[i][1];payload[1]=0;payload[2]=vectors[i][0];payload[3]=0;
        payload[4]=(uint8_t)(payload[0]+payload[2]);
        us=2000000;origin=0;started=false;invalid=false;clock_reads=0;simulated=true;
        int ret=sensor_sample_fetch(dev);simulated=false;
        struct sensor_value t,h;
        if (ret || invalid || !started || sensor_channel_get(dev,SENSOR_CHAN_AMBIENT_TEMP,&t) ||
            sensor_channel_get(dev,SENSOR_CHAN_HUMIDITY,&h) || t.val1!=vectors[i][0] || h.val1!=vectors[i][1] || t.val2 || h.val2) {
            printk("SOURCE_BUILD_SMOKE_FAIL sample=%u ret=%d invalid=%d started=%d reads=%u\n",i,ret,invalid,started,clock_reads);posix_exit(1);
        }
        printk("CONFIG_WITNESS DHT11 frame=%u start_us=%u cycles=%u temp=%d humidity=%d fractional=[%d,%d]\n",i,start_width,clock_reads,t.val1,h.val1,t.val2,h.val2);
    }
    payload[4]^=1;simulated=true;started=false;us=2000000;
    int bad=sensor_sample_fetch(dev);simulated=false;
    if (bad!=-EIO){printk("SOURCE_BUILD_SMOKE_FAIL checksum ret=%d\n",bad);posix_exit(1);}
    printk("SOURCE_BUILD_SMOKE_PASS DHT11 raw driver init/fetch/channels and corrupt-checksum rejection\n");posix_exit(0);return 0;
}
