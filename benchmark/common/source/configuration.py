"""Derive task-visible settings from selected native configuration.

These mappings decode upstream enums/macros. They are not additional task
settings: values always come from each selection's actual configuration files.
"""
import copy
import re
from benchmark.common.api import load, owned


def derived(task):
    values = {}
    text = ""
    for path in task.variant["configuration_files"]:
        if path.endswith(".yaml"):
            values.update(load(owned(path)))
        elif path.endswith(".h"):
            text += owned(path).read_text()
    settings, functions = {}, {}
    if task.sensor == "bme280":
        for part, target in (("TEMP", "temperature"), ("PRESS", "pressure"), ("HUMIDITY", "humidity")):
            enabled = [int(k.rsplit("_", 1)[1][:-1]) for k, v in values.items()
                       if v is True and re.fullmatch("CONFIG_BME280_" + part + r"_OVER_\d+X", k)]
            field = {"TEMP": "temp", "PRESS": "press", "HUMIDITY": "humid"}[part]
            match = re.search(r"\." + field + r"_oversample\s*=\s*BMX280_OSRS_X(\d+)", text)
            if len(enabled) > 1:
                raise ValueError("Conflicting source oversampling choices")
            if enabled or match:
                settings[target + "_oversampling"] = enabled[0] if enabled else int(match[1])
        if values.get("CONFIG_BME280_FILTER_OFF") or ".filter = BMX280_FILTER_OFF" in text:
            settings["filter"] = "off"
        if values.get("CONFIG_BME280_MODE_FORCED") or ".run_mode = BMX280_MODE_FORCED" in text:
            functions["acquisition"] = "forced"
    if task.source["os"] == "zephyr":
        mappings = {
            "CONFIG_BMM150_PRESET_REGULAR": {"output_data_rate_hz": 10, "repetitions_xy": 9, "repetitions_z": 15},
            "CONFIG_CCS811_DRIVE_MODE_1": {"drive_mode": 1},
            "CONFIG_DPS310_TEMP_OSR_8X": {"temperature_oversampling": 8},
            "CONFIG_DPS310_PRESS_OSR_8X": {"pressure_oversampling": 8},
            "CONFIG_ISL29035_LUX_RANGE_4K": {"full_scale_lux": 4000},
            "CONFIG_ISL29035_INTEGRATION_TIME_410": {"adc_resolution_bits": 8},
            "CONFIG_SHT3XD_REPEATABILITY_HIGH": {"repeatability": "high"},
        }
        for key, fields in mappings.items():
            if values.get(key) is True:
                settings.update(fields)
        if "CONFIG_LPS22HB_SAMPLING_RATE" in values:
            settings["output_data_rate_hz"] = {0: 0, 1: 1, 2: 10, 3: 25, 4: 50, 5: 75}[values["CONFIG_LPS22HB_SAMPLING_RATE"]]
        if values.get("CONFIG_LSM303DLHC_MAGN_RANGE") == 1:
            settings["magnetic_range_gauss"] = 1.3
        if values.get("CONFIG_LSM303DLHC_MAGN_ODR") == 4:
            settings["output_data_rate_hz"] = 15
        if values.get("CONFIG_SHT3XD_SINGLE_SHOT_MODE"):
            functions["acquisition"] = "single_shot"
        if values.get("CONFIG_DHT20_CRC"):
            functions["data_integrity"] = "CRC"
    return {key: value for key, value in (("sensor_settings", settings), ("functions", functions)) if value}


def effective(task):
    result = copy.deepcopy(task.spec["requirements"])
    for section, fields in derived(task).items():
        for name, value in fields.items():
            existing = result.setdefault(section, {})
            if name in existing and existing[name] != value:
                raise ValueError("Source/target configuration conflict: " + section + "." + name)
            existing[name] = value
    return result
