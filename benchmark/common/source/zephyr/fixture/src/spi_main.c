/* SPDX-License-Identifier: Apache-2.0 */
/* Build witness only: execute the unmodified source driver and verify its
 * selected SPI configuration. Synthetic smoke values are not target oracles. */
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
static uint8_t registers[256];
static unsigned int transactions;
static bool reset_seen;
static int initialize(const struct emul *e, const struct device *p)
{
    ARG_UNUSED(e); ARG_UNUSED(p);
    memset(registers, 0, sizeof(registers));
#ifdef CONFIG_ADT7310
    registers[3]=0xc3;
#else
    registers[0]=0xad; registers[1]=0x1d; registers[2]=0xf2;
    registers[0x0b]=1; registers[0x2c]=0x13;
    sys_put_le16(1000, &registers[0x0e]);
    sys_put_le16((uint16_t)-500, &registers[0x10]);
    sys_put_le16(250, &registers[0x12]);
#endif
    return 0;
}
static int exchange(const struct emul *e, const struct spi_config *cfg,
                    const struct spi_buf_set *tx, const struct spi_buf_set *rx)
{
    uint8_t sent[256]={0}, received[256]={0}; size_t length=0;
    if (!tx || cfg->frequency!=1000000 || cfg->slave!=0 ||
        SPI_WORD_SIZE_GET(cfg->operation)!=8 || cfg->operation & SPI_TRANSFER_LSB) return -EINVAL;
#ifdef CONFIG_ADT7310
    if ((cfg->operation & (SPI_MODE_CPOL|SPI_MODE_CPHA)) != (SPI_MODE_CPOL|SPI_MODE_CPHA)) return -EINVAL;
#else
    if (cfg->operation & (SPI_MODE_CPOL|SPI_MODE_CPHA)) return -EINVAL;
#endif
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
#ifdef CONFIG_ADT7310
    if (length<2) return -EINVAL;
    uint8_t reg=(sent[0]>>3)&7;
    if (sent[0]&0x40) {
        if (reg==2 && length>=3) sys_put_be16((uint16_t)-1600,received+1);
        else received[1]=registers[reg];
    } else registers[reg]=sent[1];
#else
    if (length<2 || (sent[0]!=0x0a && sent[0]!=0x0b)) return -EINVAL;
    uint8_t reg=sent[1];
    if (sent[0]==0x0a && reg==0x1f && length>=3 && sent[2]==0x52) {
        initialize(e,NULL); reset_seen=true;
    } else for (size_t i=2;i<length;i++,reg++) {
        if (sent[0]==0x0b) received[i]=registers[reg];
        else registers[reg]=sent[i];
    }
#endif
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
int main(void)
{
    const struct device *dev=DEVICE_DT_GET(SENSOR_NODE);
    if (!device_is_ready(dev) || sensor_sample_fetch(dev)) {printk("SOURCE_BUILD_SMOKE_FAIL: SPI device/API\n");posix_exit(1);}
#ifdef CONFIG_ADT7310
    struct sensor_value v;
    if (sensor_channel_get(dev,SENSOR_CHAN_AMBIENT_TEMP,&v) || registers[1]!=0x90 || sensor_value_to_micro(&v)!=-12500000) {
        printk("SOURCE_BUILD_SMOKE_FAIL: ADT7310 configuration/value\n");posix_exit(1);
    }
    printk("CONFIG_WITNESS ADT7310 spi_mode=3 hz=1000000 cs=0 resolution=16 continuous=1 temperature_micro_C=-12500000\n");
#else
    struct sensor_value v[3];
    if (sensor_channel_get(dev,SENSOR_CHAN_ACCEL_XYZ,v) || !reset_seen || registers[0x2c]!=0x13 || registers[0x2d]!=2 || (registers[0x28]&3)!=0 || sensor_value_to_micro(v)!=9806650 || sensor_value_to_micro(v+1)!=-4903325 || sensor_value_to_micro(v+2)!=2451662) {
        printk("SOURCE_BUILD_SMOKE_FAIL: ADXL362 configuration/value regs=%x,%x,%x reset=%d\n",registers[0x2c],registers[0x2d],registers[0x28],reset_seen);posix_exit(1);
    }
    printk("CONFIG_WITNESS ADXL362 spi_mode=0 hz=1000000 cs=0 range_g=2 odr_hz=100 fifo=off accel_micro_m_s2=[9806650,-4903325,2451662]\n");
#endif
    printk("SOURCE_BUILD_SMOKE_PASS: transactions=%u\n",transactions);posix_exit(0);return 0;
}
