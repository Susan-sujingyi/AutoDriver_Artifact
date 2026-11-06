/*
 * Copyright (c) 2018 Peter Bigot Consulting, LLC
 * Copyright (c) 2018 Linaro Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 */


#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/util.h>
#include <zephyr/drivers/sensor/ccs811.h>

/* Registers */
#define CCS811_REG_STATUS               0x00
#define CCS811_REG_MEAS_MODE            0x01
#define CCS811_REG_ALG_RESULT_DATA      0x02
#define CCS811_REG_RAW_DATA             0x03
#define CCS811_REG_ENV_DATA             0x05
#define CCS811_REG_THRESHOLDS           0x10
#define CCS811_REG_BASELINE             0x11
#define CCS811_REG_HW_ID                0x20
#define CCS811_REG_HW_VERSION           0x21
#define CCS811_REG_FW_BOOT_VERSION      0x23
#define CCS811_REG_FW_APP_VERSION       0x24
#define CCS811_REG_ERROR_ID             0xE0
#define CCS811_REG_APP_START            0xF4

#define CCS881_HW_ID                    0x81
#define CCS811_HW_VERSION_MSK           0xF0

/* Measurement modes */
#define CCS811_MODE_RAW_DATA            0x40
#define CCS811_MODE_DATARDY             0x08
#define CCS811_MODE_THRESH              0x04

#define CCS811_RAW_VOLTAGE_POS          0
#define CCS811_RAW_VOLTAGE_MSK          (0x3FF << CCS811_RAW_VOLTAGE_POS)
#define CCS811_RAW_VOLTAGE_SCALE        (1650000U / (CCS811_RAW_VOLTAGE_MSK >> CCS811_RAW_VOLTAGE_POS))
						     
#define CCS811_RAW_CURRENT_POS          10
#define CCS811_RAW_CURRENT_MSK          (0x3F << CCS811_RAW_CURRENT_POS)
#define CCS811_RAW_CURRENT_SCALE        1

#define CCS811_CO2_MIN_PPM              400
#define CCS811_CO2_MAX_PPM              32767

/* Status register fields */
#define CCS811_STATUS_ERROR             BIT(0)
#define CCS811_STATUS_DATA_READY        BIT(3)
#define CCS811_STATUS_APP_VALID         BIT(4)
#define CCS811_STATUS_FW_MODE           BIT(7)

/* Error register fields */
#define CCS811_ERROR_WRITE_REG_INVALID  BIT(0)
#define CCS811_ERROR_READ_REG_INVALID   BIT(1)
#define CCS811_ERROR_MEASMODE_INVALID   BIT(2)
#define CCS811_ERROR_MAX_RESISTANCE     BIT(3)
#define CCS811_ERROR_HEATER_FAULT       BIT(4)
#define CCS811_ERROR_HEATER_SUPPLY      BIT(5)

/* Measurement mode constants */
#define CCS811_MODE_IDLE                0x00
#define CCS811_MODE_IAQ_1SEC            0x10
#define CCS811_MODE_IAQ_10SEC           0x20
#define CCS811_MODE_IAQ_60SEC           0x30
#define CCS811_MODE_IAQ_250MSEC         0x40
#define CCS811_MODE_MSK                 0x70


struct ccs811_data {
#ifdef CONFIG_CCS811_TRIGGER
	const struct device *dev;

	/*
	 * DATARDY is configured through SENSOR_CHAN_ALL.
	 * THRESH would be configured through SENSOR_CHAN_CO2.
	 */
	struct gpio_callback gpio_cb;
	sensor_trigger_handler_t handler;
	const struct sensor_trigger *trigger;
#if defined(CONFIG_CCS811_TRIGGER_OWN_THREAD)
	K_KERNEL_STACK_MEMBER(thread_stack, CONFIG_CCS811_THREAD_STACK_SIZE);
	struct k_sem gpio_sem;
	struct k_thread thread;
#elif defined(CONFIG_CCS811_TRIGGER_GLOBAL_THREAD)
	struct k_work work;
#endif
	uint16_t co2_l2m;
	uint16_t co2_m2h;
#endif /* CONFIG_CCS811_TRIGGER */
	struct ccs811_result_type result;
	uint8_t mode;
	uint8_t app_fw_ver;
};

struct ccs811_config {
	struct i2c_dt_spec i2c;
	struct gpio_dt_spec irq_gpio;
	struct gpio_dt_spec reset_gpio;
	struct gpio_dt_spec wake_gpio;
};

#ifdef CONFIG_CCS811_TRIGGER

int ccs811_mutate_meas_mode(const struct device *dev,
			    uint8_t set,
			    uint8_t clear);

int ccs811_set_thresholds(const struct device *dev);

int ccs811_attr_set(const struct device *dev,
		    enum sensor_channel chan,
		    enum sensor_attribute attr,
		    const struct sensor_value *val);

int ccs811_trigger_set(const struct device *dev,
		       const struct sensor_trigger *trig,
		       sensor_trigger_handler_t handler);

int ccs811_init_interrupt(const struct device *dev);

#endif  /* CONFIG_CCS811_TRIGGER */

/** @brief Information collected from the sensor on each fetch. */

/**
 * @brief Access storage for the most recent data read from the sensor.
 *
 * This content of the object referenced is updated by
 * sensor_fetch_sample(), except for ccs811_result_type::mode which is
 * set on driver initialization.
 *
 * @param dev Pointer to the sensor device
 *
 * @return a pointer to the result information.
 */
const struct ccs811_result_type *ccs811_result(const struct device *dev);

/**
 * @brief Get information about static CCS811 state.
 *
 * This includes the configured operating mode as well as hardware and
 * firmware versions.
 */

/**
 * @brief Fetch operating mode and version information.
 *
 * @param dev Pointer to the sensor device
 *
 * @param ptr Pointer to where the returned information should be stored
 *
 * @return 0 on success, or a negative errno code on failure.
 */
int ccs811_configver_fetch(const struct device *dev,
			   struct ccs811_configver_type *ptr);

/**
 * @brief Fetch the current value of the BASELINE register.
 *
 * The BASELINE register encodes data used to correct sensor readings
 * based on individual device configuration and variation over time.
 *
 * For proper management of the BASELINE register see AN000370
 * "Baseline Save and Restore on CCS811".
 *
 * @param dev Pointer to the sensor device
 *
 * @return a non-negative 16-bit register value, or a negative errno
 * code on failure.
 */
int ccs811_baseline_fetch(const struct device *dev);

/**
 * @brief Update the BASELINE register.
 *
 * For proper management of the BASELINE register see AN000370
 * "Baseline Save and Restore on CCS811".
 *
 * @param dev Pointer to the sensor device
 *
 * @param baseline the value to be stored in the BASELINE register.
 *
 * @return 0 if successful, negative errno code if failure.
 */
int ccs811_baseline_update(const struct device *dev, uint16_t baseline);

/**
 * @brief Update the ENV_DATA register.
 *
 * Accurate calculation of gas levels requires accurate environment
 * data.  Measurements are only accurate to 0.5 Cel and 0.5 %RH.
 *
 * @param dev Pointer to the sensor device
 *
 * @param temperature the current temperature at the sensor
 *
 * @param humidity the current humidity at the sensor
 *
 * @return 0 if successful, negative errno code if failure.
 */
int ccs811_envdata_update(const struct device *dev,
			  const struct sensor_value *temperature,
			  const struct sensor_value *humidity);
