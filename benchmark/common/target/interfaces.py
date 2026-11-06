"""Declared product interfaces, shared by preparation, input and evaluation."""
from pathlib import Path
import shutil

from benchmark.common.api import relative


RESOURCES = {
    "gpio_waveform": {
        "files": {
            "include/nuttx/sensors/gpio_waveform.h": "benchmark/common/target/nuttx/public/resources/gpio_waveform.h",
            "boards/arm/nrf52/nrf52840-dk/src/gpio_waveform.c": "benchmark/common/target/nuttx/public/resources/gpio_waveform.c",
        },
        "interfaces": ["include/nuttx/sensors/gpio_waveform.h"],
    },
}


def installation(profile):
    support = profile["board_support"]
    api = support["driver_api"]
    files = {str(relative(api["target"])): str(relative(api["source"]))}
    for name in support["resources"]:
        for target, source in RESOURCES[name]["files"].items():
            if target in files:
                raise ValueError("Duplicate installed product resource: " + target)
            files[target] = source
    return files


def public_interfaces(profile):
    if profile["os"] == "tock":
        return ["target/boards/components/src/" + profile["module"] + ".rs"]
    installation(profile)  # validate resource keys and destination uniqueness
    names = [profile["board_support"]["driver_api"]["target"]]
    for resource in profile["board_support"]["resources"]:
        names.extend(RESOURCES[resource]["interfaces"])
    return list(dict.fromkeys("target/" + n for n in names))


def install_interfaces(root, target, profile):
    for name, source in installation(profile).items():
        dest = target / name
        if dest.exists() or dest.is_symlink():
            raise ValueError("Product resource would overwrite an existing file: " + name)
        origin = root / source
        if not origin.is_file() or origin.is_symlink():
            raise ValueError("Invalid product resource: " + source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, dest)
