"""Public NuttX configuration shared by method compilation and host builds."""


def nuttx_configuration(bus, sensor, *, automatic_startup=True):
    enabled = ["SENSORS", "SENSORS_USE_FLOAT", "BOARD_LATE_INITIALIZE",
               "SYSTEM_SENSOR_SAMPLE", "LIBC_FLOATINGPOINT", "BUILTIN", "NSH_BUILTIN_APPS"]
    disabled = ["NRF52_PWM0", "NRF52_I2C1_MASTER"]
    if bus == "spi":
        enabled += ["SPI", "SPI_EXCHANGE", "NRF52_SPI0_MASTER"]
        disabled += ["NRF52_I2C0_MASTER", "NRF52_SPI1_MASTER", "NRF52_SPI2_MASTER",
                     "NRF52_SPI3_MASTER", "LPWAN_SX127X"]
    elif bus == "gpio":
        enabled += ["ARCH_PERF_EVENTS"]
        disabled += ["NRF52_I2C0_MASTER", "NRF52_SPI0_MASTER", "NRF52_SPI1_MASTER",
                     "NRF52_SPI2_MASTER", "NRF52_SPI3_MASTER", "SCHED_TICKLESS"]
    elif bus == "i2c":
        enabled += ["I2C", "NRF52_I2C0_MASTER"]
        disabled += ["NRF52_SPI0_MASTER", "I2C_POLLED"]
    else:
        raise ValueError("Unsupported bus")
    if sensor == "isl29035":
        enabled += ["TIMER_ARCH", "ARMV7M_SYSTICK", "NRF52_SYSTIMER_SYSTICK"]
        disabled += ["SCHED_TICKLESS", "CLOCK_TIMEKEEPING", "ARCH_PERF_EVENTS"]
    tweak = ["kconfig-tweak", "--file", ".config"]
    tweak += [item for key in enabled for item in ("--enable", "CONFIG_" + key)]
    tweak += [item for key in disabled for item in ("--disable", "CONFIG_" + key)]
    if automatic_startup:
        tweak += ["--set-str", "CONFIG_INIT_ENTRYPOINT", "sensor_sample_boot_main",
                  "--set-val", "CONFIG_INIT_STACKSIZE", "4096"]
    if sensor == "isl29035":
        tweak += ["--set-val", "CONFIG_USEC_PER_TICK", "1000"]
    if bus == "gpio":
        tweak += ["--set-val", "CONFIG_USEC_PER_TICK", "10000"]
    steps = [["bash", "tools/configure.sh", "-E", "-l", "-a", "../apps", "nrf52840-dk:nsh"],
             tweak, ["make", "olddefconfig"], ["make", "-j4"]]
    return enabled, disabled, steps
