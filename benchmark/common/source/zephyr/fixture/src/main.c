/* SPDX-License-Identifier: Apache-2.0 */
/* Source-build smoke fixture only: this register bank proves that the real
 * Zephyr device initializes and the selected driver API is linked/executed.
 * It is not a datasheet model, target-driver oracle or hardware validation. */
#include <errno.h>
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/drivers/emul.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/i2c_emul.h>
#include <zephyr/drivers/sensor.h>
#ifdef CONFIG_CCS811
#include <zephyr/drivers/gpio/gpio_emul.h>
#endif
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <posix_board_if.h>

#define SENSOR_NODE DT_NODELABEL(source_sensor)
static uint8_t registers[256];
static unsigned int transactions;
#ifdef CONFIG_CCS811
static const struct gpio_dt_spec wake = GPIO_DT_SPEC_GET(SENSOR_NODE, wake_gpios);
static bool reset_seen, app_started;
#endif
#if defined(CONFIG_BH1750) || defined(CONFIG_DHT20) || defined(CONFIG_SHT3XD)
static int64_t measurement_started_ms, measurement_wait_ms;
static unsigned int measurement_commands, data_reads;
static bool status_initialized;
#endif
#ifdef CONFIG_DPS310
static int64_t dps_started_ms;
static uint8_t dps_pending_command;
static unsigned int dps_temperature_commands, dps_pressure_commands;
#endif
#if defined(CONFIG_BMM150) || defined(CONFIG_LSM6DSO)
static bool source_reset_seen;
#endif
#ifdef CONFIG_LSM6DSO
static uint8_t sensor_hub_registers[256];
#endif

static int register_bank_init(const struct emul *emul, const struct device *parent)
{
	ARG_UNUSED(emul);
	ARG_UNUSED(parent);
#ifdef CONFIG_LSM303DLHC_MAGN
	registers[0x09] = 1;
	sys_put_be16(1100, &registers[0x03]);
	sys_put_be16((uint16_t)-980, &registers[0x05]);
	sys_put_be16(550, &registers[0x07]);
#elif defined(CONFIG_LPS22HB)
	BUILD_ASSERT(CONFIG_LPS22HB_SAMPLING_RATE == 1);
	registers[0x0f] = 0xb1;
	/* 1000 hPa and -12.34 C; a single fixed smoke sample. */
	registers[0x28] = 0x00;
	registers[0x29] = 0x80;
	registers[0x2a] = 0x3e;
	sys_put_le16((uint16_t)-1234, &registers[0x2b]);
#elif defined(CONFIG_BME280)
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
#elif defined(CONFIG_ISL29035)
	BUILD_ASSERT(CONFIG_ISL29035_INTEGRATION_TIME_410);
	registers[0x0f] = 0xa8; /* ID with blownout bit set to exercise clearing. */
	registers[0x02] = 0x80; /* 128/256 of the selected 4000-lux range. */
#elif defined(CONFIG_APDS9960)
	registers[0x92] = 0xab;
	registers[0x9c] = 107; /* Fixed proximity value outside source [0,1] thresholds. */
#elif defined(CONFIG_CCS811)
	registers[0x00] = 0x10; /* APP_VALID in boot mode. */
	registers[0x20] = 0x81;
	registers[0x24] = 0x20; /* FW 2.0: source must honor DATA_READY. */
	sys_put_be16(650, &registers[0x02]);
	sys_put_be16(31, &registers[0x04]);
	registers[0x06] = 0x98;
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
#elif defined(CONFIG_MPU6050)
	registers[0x75] = 0x68;
	registers[0x6b] = 0x40; /* Sleep bit set before the source wake-up. */
	sys_put_be16(16384, &registers[0x3b]);
	sys_put_be16((uint16_t)-8192, &registers[0x3d]);
	sys_put_be16(4096, &registers[0x3f]);
#elif defined(CONFIG_DPS310)
	registers[0x0d] = 0x10;
	registers[0x08] = 0xc0;
	registers[0x28] = 0x80;
	/* c0=40, c1=10, c00=100000, c10=2000, c01=2000, other terms zero. */
	static const uint8_t coefficients[18] = {
		0x02, 0x80, 0x0a, 0x18, 0x6a, 0x00, 0x07, 0xd0, 0x07, 0xd0
	};
	memcpy(&registers[0x10], coefficients, sizeof(coefficients));
	sys_put_be24(7864320, &registers[0x00]);
	sys_put_be24(3932160, &registers[0x03]);
#elif defined(CONFIG_BMM150)
	registers[0x40] = 0x32;
	registers[0x4c] = 0x06;
	/* Benign synthetic trims: rhall=xyz1=1000, z1=32768, z2=16384. */
	sys_put_le16(16384, &registers[0x68]);
	sys_put_le16(32768, &registers[0x6a]);
	sys_put_le16(1000, &registers[0x6c]);
	sys_put_le16(100 << 3, &registers[0x42]);
	sys_put_le16((uint16_t)(-200 * 8), &registers[0x44]);
	sys_put_le16(300 << 1, &registers[0x46]);
	sys_put_le16((1000 << 2) | 1, &registers[0x48]);
#elif defined(CONFIG_LSM6DSO)
	registers[0x0f] = 0x6c;
	registers[0x12] = 0x04; /* Reset IF_INC default. */
	sys_put_le16(1000, &registers[0x28]);
	sys_put_le16((uint16_t)-500, &registers[0x2a]);
	sys_put_le16(250, &registers[0x2c]);
#endif
	return 0;
}

#if defined(CONFIG_BH1750) || defined(CONFIG_DHT20) || defined(CONFIG_SHT3XD)
/* Fixed command/response smoke samples. This exercises real source timing and
 * APIs, not a full datasheet protocol model or independent target oracle. */
static int command_sensor_transfer(struct i2c_msg *msgs, int count)
{
#ifdef CONFIG_BH1750
	BUILD_ASSERT(DT_PROP(SENSOR_NODE, resolution) == 1);
	BUILD_ASSERT(DT_PROP(SENSOR_NODE, mtreg) == 69);
	if (!(msgs[0].flags & I2C_MSG_READ) && msgs[0].len == 1 && msgs[0].buf[0] == 0x20) {
		measurement_commands++;
		if (count == 1) {
			measurement_started_ms = k_uptime_get();
			return 0;
		}
		if (count == 2 && (msgs[1].flags & I2C_MSG_READ) && msgs[1].len == 2) {
			measurement_wait_ms = k_uptime_get() - measurement_started_ms;
			if (measurement_wait_ms < 180) {
				return -EIO;
			}
			/* The source writes opcode 0x20 again before reading. Supply a
			 * fixed sample while explicitly recording this source defect. */
			sys_put_be16(1200, msgs[1].buf);
			data_reads++;
			return 0;
		}
	}
#elif defined(CONFIG_DHT20)
	BUILD_ASSERT(CONFIG_DHT20_CRC);
	if (count == 2 && !(msgs[0].flags & I2C_MSG_READ) && msgs[0].len == 1 &&
	    msgs[0].buf[0] == 0x71 && (msgs[1].flags & I2C_MSG_READ) && msgs[1].len == 1) {
		msgs[1].buf[0] = 0x18; /* Smoke selects already calibrated startup. */
		status_initialized = true;
		return 0;
	}
	if (count == 1 && !(msgs[0].flags & I2C_MSG_READ) && msgs[0].len == 3) {
		static const uint8_t trigger[] = {0xac, 0x33, 0x00};
		if (memcmp(msgs[0].buf, trigger, sizeof(trigger))) {
			return -EIO;
		}
		measurement_commands++;
		measurement_started_ms = k_uptime_get();
		return 0;
	}
	if (count == 1 && (msgs[0].flags & I2C_MSG_READ) && msgs[0].len == 7) {
		/* RH raw=0x80000, T raw=0x60000; polynomial 0x31/init 0xff CRC=0x23. */
		static const uint8_t frame[] = {0x18, 0x80, 0x00, 0x06, 0x00, 0x00, 0x23};
		measurement_wait_ms = k_uptime_get() - measurement_started_ms;
		if (!measurement_commands || measurement_wait_ms < 80) {
			return -EIO;
		}
		memcpy(msgs[0].buf, frame, sizeof(frame));
		data_reads++;
		return 0;
	}
#elif defined(CONFIG_SHT3XD)
	BUILD_ASSERT(CONFIG_SHT3XD_SINGLE_SHOT_MODE && CONFIG_SHT3XD_REPEATABILITY_HIGH);
	if (count == 1 && !(msgs[0].flags & I2C_MSG_READ) && msgs[0].len == 2) {
		uint16_t command = sys_get_be16(msgs[0].buf);
		if (command == 0x3041) {
			status_initialized = true;
			return 0;
		}
		if (command == 0x2400) {
			measurement_commands++;
			measurement_started_ms = k_uptime_get();
			return 0;
		}
	}
	if (count == 1 && (msgs[0].flags & I2C_MSG_READ) && msgs[0].len == 6) {
		/* Independent fixed bytes: T=0x6666/CRC93, RH=0x8000/CRCa2. */
		static const uint8_t frame[] = {0x66, 0x66, 0x93, 0x80, 0x00, 0xa2};
		measurement_wait_ms = k_uptime_get() - measurement_started_ms;
		if (!measurement_commands || measurement_wait_ms < 15) {
			return -EIO;
		}
		memcpy(msgs[0].buf, frame, sizeof(frame));
		data_reads++;
		return 0;
	}
#endif
	return -EIO;
}
#endif

static int transfer(const struct emul *emul, struct i2c_msg *msgs, int count, int addr)
{
	ARG_UNUSED(emul);
	if (addr != DT_REG_ADDR(SENSOR_NODE) || count < 1 || msgs[0].len < 1) {
		return -EIO;
	}
	transactions++;
#if defined(CONFIG_BH1750) || defined(CONFIG_DHT20) || defined(CONFIG_SHT3XD)
	return command_sensor_transfer(msgs, count);
#endif
	if (msgs[0].flags & I2C_MSG_READ) {
		return -EIO;
	}
	unsigned int reg = msgs[0].buf[0];
	uint8_t *bank = registers;
#ifdef CONFIG_LSM6DSO
	if (reg != 0x01 && (registers[0x01] & 0xc0) == 0x40) {
		bank = sensor_hub_registers;
	}
#endif
#ifdef CONFIG_CCS811
	/* Real Zephyr GPIO-emulator output must be active throughout transfer. */
	if (gpio_emul_output_get_dt(&wake) != 0) {
		return -EIO;
	}
	if (reg == 0xff && count == 1 && msgs[0].len == 5) {
		static const uint8_t reset[] = {0xff, 0x11, 0xe5, 0x72, 0x8a};
		if (memcmp(msgs[0].buf, reset, sizeof(reset))) {
			return -EIO;
		}
		reset_seen = true;
		registers[0x00] = 0x10;
		registers[0x01] = 0;
		return 0;
	}
	if (reg == 0xf4 && count == 1 && msgs[0].len == 1) {
		app_started = true;
		registers[0x00] = 0x98;
		return 0;
	}
#elif defined(CONFIG_APDS9960)
	/* Deliberately supply the status the source polls: PINT, not PVALID.
	 * This is one threshold-crossing sample, not a complete device model. */
	if (reg == 0x93 && count == 2 && (registers[0x80] & 0x05) == 0x05) {
		registers[0x93] = 0x22;
	}
	if (reg == 0xe7 && count == 1) {
		registers[0x93] = 0;
	}
#elif defined(CONFIG_DPS310)
	if (reg == 0x08 && count == 1 && msgs[0].len == 2) {
		dps_pending_command = msgs[0].buf[1];
		if (dps_pending_command == 2) {
			dps_temperature_commands++;
		} else if (dps_pending_command == 1) {
			dps_pressure_commands++;
		} else {
			return -EIO;
		}
		dps_started_ms = k_uptime_get();
		registers[0x08] = 0xc0 | dps_pending_command;
		return 0;
	}
	if (reg == 0x08 && count == 2 && dps_pending_command &&
	    k_uptime_get() - dps_started_ms >= 15) {
		registers[0x08] |= dps_pending_command == 2 ? 0x20 : 0x10;
	}
#endif
	for (int i = 0; i < count; i++) {
		unsigned int start = i == 0 ? 1 : 0;
		for (unsigned int j = start; j < msgs[i].len; j++) {
			if (reg >= sizeof(registers)) {
				return -EIO;
			}
			if (msgs[i].flags & I2C_MSG_READ) {
				msgs[i].buf[j] = bank[reg++];
			} else {
#ifdef CONFIG_BMM150
				if (reg == 0x4b && (msgs[i].buf[j] & 0x82)) {
					source_reset_seen = true;
					registers[0x4c] = 0x06;
					bank[reg++] = msgs[i].buf[j] & 0x01;
					continue;
				}
#elif defined(CONFIG_LSM6DSO)
				if (bank == registers && reg == 0x12 && (msgs[i].buf[j] & 0x01)) {
					source_reset_seen = true;
					bank[reg++] = 0x04; /* Fixed completed reset, IF_INC default retained. */
					continue;
				}
#endif
				bank[reg++] = msgs[i].buf[j];
			}
		}
	}
#ifdef CONFIG_DPS310
	registers[0x08] |= 0xc0; /* Read-only calibration/sensor ready bits. */
#endif
	return 0;
}

static const struct i2c_emul_api bus_api = {.transfer = transfer};
EMUL_DT_DEFINE(SENSOR_NODE, register_bank_init, NULL, NULL, &bus_api, NULL);

int main(void)
{
	const struct device *dev = DEVICE_DT_GET(SENSOR_NODE);
	const enum sensor_channel smoke_channel =
#ifdef CONFIG_LSM6DSO
		SENSOR_CHAN_ACCEL_XYZ;
#else
		SENSOR_CHAN_ALL;
#endif
	if (!device_is_ready(dev) || sensor_sample_fetch_chan(dev, smoke_channel) != 0) {
		printk("SOURCE_BUILD_SMOKE_FAIL: device/API unavailable\n");
		posix_exit(1);
	}
#if defined(CONFIG_LPS22HB) || defined(CONFIG_BME280)
	struct sensor_value temperature, pressure;
	if (sensor_channel_get(dev, SENSOR_CHAN_AMBIENT_TEMP, &temperature) != 0 ||
	    sensor_channel_get(dev, SENSOR_CHAN_PRESS, &pressure) != 0) {
		printk("SOURCE_BUILD_SMOKE_FAIL: temperature/pressure channels unavailable\n");
		posix_exit(1);
	}
#endif
#ifdef CONFIG_LSM303DLHC_MAGN
    struct sensor_value magnetic[3];
    if (sensor_channel_get(dev, SENSOR_CHAN_MAGN_XYZ, magnetic) != 0 ||
        registers[0] != 0x10 || registers[1] != 0x20 || registers[2] != 0 ||
        sensor_value_to_micro(&magnetic[0]) != 1000000 ||
        sensor_value_to_micro(&magnetic[1]) != 500000 ||
        sensor_value_to_micro(&magnetic[2]) != -1000000) {
        printk("SOURCE_BUILD_SMOKE_FAIL: LSM303DLHC source configuration unavailable\n");
        posix_exit(1);
    }
    printk("CONFIG_WITNESS LSM303DLHC range_gauss=1.3 odr_hz=15 magnetic_micro_gauss=[1000000,500000,-1000000]\n");
#elif defined(CONFIG_LPS22HB)
	if (registers[0x10] != 0x12 || transactions < 6) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected ODR/BDU not written\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS LPS22HB rate_hz=1 ctrl1=0x%02x\n", registers[0x10]);
#elif defined(CONFIG_BME280)
	struct sensor_value humidity;
	if (sensor_channel_get(dev, SENSOR_CHAN_HUMIDITY, &humidity) != 0 ||
	    registers[0xf2] != 0x01 || registers[0xf4] != 0x35 ||
	    (registers[0xf5] & 0x1c) != 0) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected BME280 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS BME280 ctrl_hum=0x%02x ctrl_meas=0x%02x filter=off\n",
	       registers[0xf2], registers[0xf4]);
#elif defined(CONFIG_ISL29035)
	struct sensor_value light;
	if (sensor_channel_get(dev, SENSOR_CHAN_LIGHT, &light) != 0 ||
	    registers[0x00] != 0xa0 || registers[0x01] != 0x09 ||
	    (registers[0x0f] & 0x80) || light.val1 != 2000 || light.val2 != 0) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected ISL29035 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS ISL29035 command1=0x%02x command2=0x%02x adc_bits=8 integration_us=410 light_lux=%d\n",
	       registers[0x00], registers[0x01], light.val1);
#elif defined(CONFIG_APDS9960)
	struct sensor_value proximity;
	if (sensor_channel_get(dev, SENSOR_CHAN_PROX, &proximity) != 0 ||
	    proximity.val1 != 107 || proximity.val2 != 0 || registers[0x80] != 0x05 ||
	    registers[0x8e] != 0x07 || (registers[0x8f] & 0x0c) != 0 ||
	    registers[0x90] != 0x51 || registers[0x89] != 0 || registers[0x8b] != 1) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected APDS9960 source behavior unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS APDS9960 enable=0x%02x ppulse=0x%02x pgain_bits=0x%02x config2=0x%02x proximity=%d\n",
	       registers[0x80], registers[0x8e], registers[0x8f] & 0x0c, registers[0x90], proximity.val1);
	printk("SOURCE_BEHAVIOR APDS9960 polls=PINT threshold=[0,1] source_ppulse=4us source_pgain=1x; DT defaults differ, not target correctness evidence\n");
#elif defined(CONFIG_CCS811)
	struct sensor_value co2, voc;
	if (sensor_channel_get(dev, SENSOR_CHAN_CO2, &co2) != 0 ||
	    sensor_channel_get(dev, SENSOR_CHAN_VOC, &voc) != 0 ||
	    co2.val1 != 650 || co2.val2 != 0 || voc.val1 != 31 || voc.val2 != 0 ||
	    registers[0x01] != 0x10 || !reset_seen || !app_started ||
	    gpio_emul_output_get_dt(&wake) != 1) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected CCS811 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS CCS811 meas_mode=0x%02x reset=1 app_start=1 wake_gpio_active_low=1 co2_ppm=%d voc_ppb=%d\n",
	       registers[0x01], co2.val1, voc.val1);
#elif defined(CONFIG_BH1750)
	struct sensor_value light;
	if (sensor_channel_get(dev, SENSOR_CHAN_LIGHT, &light) != 0 ||
	    light.val1 != 1000 || light.val2 != 0 || measurement_commands != 2 || data_reads != 1) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected BH1750 source behavior unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS BH1750 resolution=1 mtreg=69 opcode=0x20 wait_ms=%lld light_lux=%d\n",
	       measurement_wait_ms, light.val1);
	printk("SOURCE_BEHAVIOR BH1750 opcode_writes=2 final_read=write_opcode_then_read; fixed sample does not establish datasheet protocol correctness\n");
#elif defined(CONFIG_DHT20)
	struct sensor_value temperature, humidity;
	if (sensor_channel_get(dev, SENSOR_CHAN_AMBIENT_TEMP, &temperature) != 0 ||
	    sensor_channel_get(dev, SENSOR_CHAN_HUMIDITY, &humidity) != 0 ||
	    temperature.val1 != 25 || temperature.val2 != 0 || humidity.val1 != 50 || humidity.val2 != 0 ||
	    !status_initialized || measurement_commands != 1 || data_reads != 1) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected DHT20 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS DHT20 calibrated_status=0x18 trigger=ac3300 crc=on frame_crc=0x23 wait_ms=%lld temperature_C=%d humidity_percent=%d\n",
	       measurement_wait_ms, temperature.val1, humidity.val1);
#elif defined(CONFIG_SHT3XD)
	struct sensor_value temperature, humidity;
	if (sensor_channel_get(dev, SENSOR_CHAN_AMBIENT_TEMP, &temperature) != 0 ||
	    sensor_channel_get(dev, SENSOR_CHAN_HUMIDITY, &humidity) != 0 ||
	    temperature.val1 != 25 || temperature.val2 != 0 || humidity.val1 != 50 || humidity.val2 != 762 ||
	    !status_initialized || measurement_commands != 1 || data_reads != 1) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected SHT30 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS SHT30 alias=sht3x clear_status=0x3041 measurement=0x2400 crc=on wait_ms=%lld temperature_C=%d humidity_percent=%d.%06d\n",
	       measurement_wait_ms, temperature.val1, humidity.val1, humidity.val2);
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
#elif defined(CONFIG_MPU6050)
	struct sensor_value acceleration[3];
	if (sensor_channel_get(dev, SENSOR_CHAN_ACCEL_XYZ, acceleration) != 0 ||
	    registers[0x6b] != 0 || registers[0x1c] != 0 || registers[0x1b] != 0 || registers[0x19] != 9 ||
	    sensor_value_to_micro(&acceleration[0]) != 9806650 ||
	    sensor_value_to_micro(&acceleration[1]) != -4903325 ||
	    sensor_value_to_micro(&acceleration[2]) != 2451662) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected MPU6050 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS MPU6050 who_am_i=0x68 power=0x00 accel_cfg=0x00 gyro_cfg=0x00 smplrt_div=9 accel_micro_m_s2=[9806650,-4903325,2451662]\n");
	printk("SOURCE_BEHAVIOR MPU6050 source fetch directly reads 14 bytes without data-ready polling; fixed-sample smoke is not freshness evidence\n");
#elif defined(CONFIG_DPS310)
	struct sensor_value temperature, pressure;
	if (sensor_channel_get(dev, SENSOR_CHAN_AMBIENT_TEMP, &temperature) != 0 ||
	    sensor_channel_get(dev, SENSOR_CHAN_PRESS, &pressure) != 0 ||
	    registers[0x06] != 0x03 || registers[0x07] != 0x83 || registers[0x09] != 0 ||
	    registers[0x62] != 0x02 || dps_temperature_commands != 2 || dps_pressure_commands != 2 ||
	    temperature.val1 != 25 || temperature.val2 != 0 || pressure.val1 != 103 || pressure.val2 != 0) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected DPS310 configuration unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS DPS310 prs_cfg=0x03 tmp_cfg=0x83 shifts=0 workaround=0x02 temp_commands=2 pressure_commands=2 temperature_C=25 pressure_kPa=103\n");
#elif defined(CONFIG_BMM150)
	struct sensor_value magnetic[3];
	if (sensor_channel_get(dev, SENSOR_CHAN_MAGN_XYZ, magnetic) != 0 || !source_reset_seen ||
	    registers[0x4b] != 0x01 || registers[0x4c] != 0 || registers[0x51] != 0x04 || registers[0x52] != 0x07 ||
	    sensor_value_to_micro(&magnetic[0]) != 312500 ||
	    sensor_value_to_micro(&magnetic[1]) != -625000 ||
	    sensor_value_to_micro(&magnetic[2]) != 353125) {
		printk("SOURCE_BUILD_SMOKE_FAIL: selected BMM150 source behavior unavailable\n");
		posix_exit(1);
	}
	printk("CONFIG_WITNESS BMM150 power=0x01 mode_odr=0x00 rep_xy=0x04 rep_z=0x07 magnetic_micro_gauss=[312500,-625000,353125]\n");
	printk("SOURCE_BEHAVIOR BMM150 regular preset Z=15 uses XY encoding and writes 7; source direct fetch does not poll data-ready; synthetic trims are not hardware calibration\n");
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
	printk("SOURCE_BUILD_SMOKE_PASS device=%s transactions=%u\n", dev->name, transactions);
	posix_exit(0);
	return 0;
}
