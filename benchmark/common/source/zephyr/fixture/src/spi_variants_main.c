/* SPDX-License-Identifier: Apache-2.0 */
/* SPI source-build witness only. Synthetic smoke is not the independent oracle. */
#include <errno.h>
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/drivers/emul.h>
#include <zephyr/drivers/spi_emul.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <posix_board_if.h>
#define SENSOR_NODE DT_NODELABEL(source_sensor)
static uint8_t registers[256], sensor_hub_registers[256];
static unsigned int transactions;
static bool source_reset_seen;
static int initialize(const struct emul *e,const struct device *p) {
 ARG_UNUSED(e);ARG_UNUSED(p);
#ifdef CONFIG_BME280
	registers[0xd0] = 0x60;
	/* Nonzero calibration and raw bytes exercise the compiled compensation
	 * code. Their output is deliberately not an independent numeric oracle. */
	sys_put_le16(27504, &registers[0x88]);
	sys_put_le16(26435, &registers[0x8a]);
	sys_put_le16((uint16_t)-1000, &registers[0x8c]);
	sys_put_le16(36477, &registers[0x8e]);
	registers[0xf7] = 0x65;
	registers[0xf8] = 0x5a;
	registers[0xf9] = 0xc0;
	registers[0xfa] = 0x7e;
	registers[0xfb] = 0xed;
	registers[0xfc] = 0x00;
#elif defined(CONFIG_BMP388)
	registers[0x00] = 0x50;
	registers[0x03] = 0x60; /* Fixed ready sample; source busy-polls without a timeout. */
	/* Synthetic linear compensation smoke profile, not factory trim data. */
	sys_put_le16(32768, &registers[0x31]);
	sys_put_le16(32768, &registers[0x33]);
	sys_put_le16(17408, &registers[0x36]);
	sys_put_le16(16384, &registers[0x38]);
	sys_put_le16(10000, &registers[0x3c]);
	sys_put_le16(64, &registers[0x3e]);
	sys_put_le24(8388608, &registers[0x04]);
	sys_put_le24(9207808, &registers[0x07]);
#elif defined(CONFIG_LSM6DSO)
	registers[0x0f] = 0x6c;
	registers[0x12] = 0x04; /* Reset IF_INC default. */
	sys_put_le16(1000, &registers[0x28]);
	sys_put_le16((uint16_t)-500, &registers[0x2a]);
	sys_put_le16(250, &registers[0x2c]);
#endif
 return 0;
}
static int exchange(const struct emul *e, const struct spi_config *cfg,
                    const struct spi_buf_set *tx, const struct spi_buf_set *rx)
{
    uint8_t sent[256]={0}, received[256]={0}; size_t length=0;
    if (!tx || cfg->frequency!=1000000 || cfg->slave!=0 ||
        SPI_WORD_SIZE_GET(cfg->operation)!=8 || cfg->operation & SPI_TRANSFER_LSB) return -EINVAL;
    if ((cfg->operation & (SPI_MODE_CPOL|SPI_MODE_CPHA)) != (SPI_MODE_CPOL|SPI_MODE_CPHA)) return -EINVAL;
    for (size_t i=0;i<tx->count;i++) {
        if (length+tx->buffers[i].len>sizeof(sent)) return -ENOMEM;
        if (tx->buffers[i].buf) memcpy(sent+length,tx->buffers[i].buf,tx->buffers[i].len);
        length+=tx->buffers[i].len;
    }
    /* Zephyr may provide only command/address TX bytes and a longer RX list;
     * the controller clocks the remaining bytes while transmitting dummy zero. */
    size_t rx_length=0;
    if (rx) for (size_t i=0;i<rx->count;i++) rx_length+=rx->buffers[i].len;
    if (rx_length>sizeof(sent)) return -ENOMEM;
    if (rx_length>length) length=rx_length;
    transactions++;
    if(length<2)return -EINVAL;
    bool reading=(sent[0]&0x80)!=0;
    unsigned int reg=sent[0]&0x7f;
    size_t prefix=1;
#ifdef CONFIG_BME280
    reg|=0x80;
#elif defined(CONFIG_BMP388)
    if(reading)prefix=2;
#endif
    uint8_t *bank=registers;
#ifdef CONFIG_LSM6DSO
    if(reg!=1 && (registers[1]&0xc0)==0x40)bank=sensor_hub_registers;
#endif
    if(reading) {
        for(size_t i=prefix;i<length;i++,reg++)received[i]=bank[reg];
    } else {
#ifdef CONFIG_LSM6DSO
        for(size_t i=1;i<length;i++,reg++) {
            if(bank==registers && reg==0x12 && (sent[i]&1)) {source_reset_seen=true;bank[reg]=4;}
            else bank[reg]=sent[i];
        }
#else
        if(length%2)return -EINVAL;
        for(size_t i=0;i<length;i+=2) {
            unsigned int address=sent[i]&0x7f;
#ifdef CONFIG_BME280
            address|=0x80;
#endif
            registers[address]=sent[i+1];
        }
#endif
    }
    size_t offset=0;
    if (rx) for (size_t i=0;i<rx->count;i++) {
        if (offset+rx->buffers[i].len>sizeof(received)) return -ENOMEM;
        if (rx->buffers[i].buf) memcpy(rx->buffers[i].buf,received+offset,rx->buffers[i].len);
        offset+=rx->buffers[i].len;
    }
    return 0;
}
static const struct spi_emul_api api={.io=exchange};
EMUL_DT_DEFINE(SENSOR_NODE, initialize, NULL, NULL, &api, NULL);
int main(void) {
 const struct device *dev=DEVICE_DT_GET(SENSOR_NODE);
 const enum sensor_channel channel=
#ifdef CONFIG_LSM6DSO
 SENSOR_CHAN_ACCEL_XYZ;
#else
 SENSOR_CHAN_ALL;
#endif
 bool ready=device_is_ready(dev);
 int fetch=ready?sensor_sample_fetch_chan(dev,channel):-ENODEV;
 if(!ready||fetch){printk("SOURCE_BUILD_SMOKE_FAIL: SPI device/API ready=%d init_result=%u fetch=%d transactions=%u\n",ready,dev->state->init_res,fetch,transactions);posix_exit(1);}
#ifdef CONFIG_BME280
 struct sensor_value temperature,pressure;
 if(sensor_channel_get(dev,SENSOR_CHAN_AMBIENT_TEMP,&temperature)||sensor_channel_get(dev,SENSOR_CHAN_PRESS,&pressure))posix_exit(1);

	struct sensor_value humidity;
	if (sensor_channel_get(dev, SENSOR_CHAN_HUMIDITY, &humidity) != 0 ||
	    registers[0xf2] != 0x01 || registers[0xf4] != 0x35 ||
	    (registers[0xf5] & 0x1c) != 0) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected BME280 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS BME280 ctrl_hum=0x%02x ctrl_meas=0x%02x filter=off\n",
	       registers[0xf2], registers[0xf4]);
#elif defined(CONFIG_BMP388)
	struct sensor_value temperature, pressure;
	if (sensor_channel_get(dev, SENSOR_CHAN_DIE_TEMP, &temperature) != 0 ||
	    sensor_channel_get(dev, SENSOR_CHAN_PRESS, &pressure) != 0 ||
	    registers[0x7e] != 0xb6 || registers[0x1d] != 0x03 || registers[0x1c] != 0x02 ||
	    registers[0x1f] != 0 || registers[0x1b] != 0x33 ||
	    temperature.val1 != 25 || temperature.val2 != 0 || pressure.val1 != 88 || pressure.val2 != 217000) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected BMP388 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS BMP388 reset=0xb6 odr=0x03 osr=0x02 iir=0x00 power=0x33 die_temperature_C=%d pressure_kPa=%d.%06d\n",
	       temperature.val1, pressure.val1, pressure.val2);
	printk("SOURCE_BEHAVIOR BMP388 smoke supplies DRDY_PRESS; source polling loop has no timeout; trims are synthetic, not hardware calibration\n");
#elif defined(CONFIG_LSM6DSO)
	struct sensor_value acceleration[3];
	if (sensor_channel_get(dev, SENSOR_CHAN_ACCEL_XYZ, acceleration) != 0 || !source_reset_seen ||
	    registers[0x01] != 0 || registers[0x10] != 0x40 || registers[0x11] != 0 ||
	    registers[0x12] != 0x44 || !(registers[0x18] & 0x02) || registers[0x0a] != 0 ||
	    sensor_value_to_micro(&acceleration[0]) != 598205 ||
	    sensor_value_to_micro(&acceleration[1]) != -299102 ||
	    sensor_value_to_micro(&acceleration[2]) != 149551) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected LSM6DSO configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS LSM6DSO vendor_hal_st=1 reset=1 ctrl1_xl=0x40 ctrl2_g=0x00 ctrl3_c=0x44 i3c_disabled=1 fifo_bypass=1 accel_micro_m_s2=[598205,-299102,149551]\n");
	printk("SOURCE_BEHAVIOR LSM6DSO direct accel fetch has no data-ready polling and ignores nested helper errors; fixed success smoke does not validate recovery or freshness\n");
#endif
 printk("SPI_WITNESS mode=3 hz=1000000 cs=0\n");
 printk("SOURCE_BUILD_SMOKE_PASS device=%s transactions=%u\n",dev->name,transactions);posix_exit(0);return 0;
}
