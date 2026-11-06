# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.0)
if(NOT ARTIFACT_ROOT)
  message(FATAL_ERROR "Supply ARTIFACT_ROOT")
endif()
if(NOT SOURCE_BUILD_DEVICE MATCHES "^(lps22hb|bme280|isl29035|apds9960|ccs811|bh1750|dht20|sht3x|bmp388|mpu6050|dps310|bmm150|lsm6dso|lsm303dlhc|adt7310|adxl362|bme280_spi|bmp388_spi|lsm6dso_spi|dht11)$")
  message(FATAL_ERROR "Unsupported SOURCE_BUILD_DEVICE")
endif()
# CONF_FILE is supplied by the release validator.
# DTC_OVERLAY_FILE is supplied by the release validator.
find_package(Zephyr REQUIRED HINTS "${ARTIFACT_ROOT}/third_party/zephyr/share/zephyr-package/cmake")
project(driverbench_source_build)
if(SOURCE_BUILD_DEVICE STREQUAL "dht11")
  target_sources(app PRIVATE src/dht11_main.c)
  zephyr_ld_options(-Wl,--wrap=sys_clock_cycle_get_32 -Wl,--wrap=arch_busy_wait)
elseif(SOURCE_BUILD_DEVICE MATCHES "^(bme280_spi|bmp388_spi|lsm6dso_spi)$")
  target_sources(app PRIVATE src/spi_variants_main.c)
elseif(SOURCE_BUILD_DEVICE MATCHES "^(adt7310|adxl362)$")
  target_sources(app PRIVATE src/spi_main.c)
else()
  target_sources(app PRIVATE src/main.c)
endif()

# Retain Zephyr's own Kconfig, driver library, headers and link integration.
# Change only the library's source selection to compile the benchmark input
# itself, so an identical-looking upstream source cannot substitute for it.
if(SOURCE_BUILD_DEVICE STREQUAL "lps22hb")
  set_property(TARGET drivers__sensor__st__lps22hb PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/st/lps22hb/lps22hb.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "bme280_spi")
  set_property(TARGET drivers__sensor__bosch__bme280 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bme280/bme280.c"
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bme280/bme280_spi.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "bme280")
  set_property(TARGET drivers__sensor__bosch__bme280 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bme280/bme280.c"
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bme280/bme280_i2c.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "isl29035")
  set_property(TARGET drivers__sensor__renesas__isl29035 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/renesas/isl29035/isl29035.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "apds9960")
  set_property(TARGET drivers__sensor__apds9960 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/benchmark/apds9960/source/zephyr/selected/apds9960.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "ccs811")
  set_property(TARGET drivers__sensor__ams__ccs811 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/ams/ccs811/ccs811.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "bh1750")
  set_property(TARGET drivers__sensor__rohm__bh1750 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/rohm/bh1750/bh1750.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "dht20")
  set_property(TARGET drivers__sensor__aosong__dht20 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/aosong/dht20/dht20.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "sht3x")
  set_property(TARGET drivers__sensor__sensirion__sht3xd PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/sensirion/sht3xd/sht3xd.c")
elseif(SOURCE_BUILD_DEVICE MATCHES "^(bmp388|bmp388_spi)$")
  set_property(TARGET drivers__sensor__bosch__bmp388 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bmp388/bmp388.c"
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bmp388/bmp388_i2c.c"
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bmp388/bmp388_spi.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "mpu6050")
  set_property(TARGET drivers__sensor__tdk__mpu6050 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/tdk/mpu6050/mpu6050.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "dps310")
  set_property(TARGET drivers__sensor__infineon__dps310 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/infineon/dps310/dps310.c")
elseif(SOURCE_BUILD_DEVICE STREQUAL "bmm150")
  set_property(TARGET drivers__sensor__bosch__bmm150 PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bmm150/bmm150.c"
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bmm150/bmm150_i2c.c"
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/bosch/bmm150/bmm150_spi.c")
elseif(SOURCE_BUILD_DEVICE MATCHES "^(lsm6dso|lsm6dso_spi)$")
  set_property(TARGET drivers__sensor__st__lsm6dso PROPERTY SOURCES
    "${ARTIFACT_ROOT}/third_party/zephyr/drivers/sensor/st/lsm6dso/lsm6dso.c")
endif()
