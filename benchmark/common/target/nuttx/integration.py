"""Derived NuttX targets, separately identified from immutable upstream exports.

Preparation is not process isolation. The prepared tree must be mounted by the
agent sandbox; a path in the artifact alone is not an access boundary.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil

from benchmark.common.api import digest, identity, inventory, relative, save

BOARD = 'boards/arm/nrf52/nrf52840-dk/src/nrf52_bringup.c'


def strict_inventory(tree: Path) -> dict:
    if tree.is_symlink():
        raise ValueError('Linked target root is not allowed')
    files = inventory(tree)
    if any('symlink' in value for value in files.values()):
        raise ValueError('Links are not allowed in a prepared target')
    forbidden = {'.git', '.hg', '.svn', '__pycache__', '.cache'}
    if any(forbidden.intersection(dirs + names) for _, dirs, names in os.walk(tree)):
        raise ValueError('History/cache in a prepared target')
    return files


def apply_recipe(tree: Path, recipe: dict) -> None:
    for change in recipe['changes']:
        path = tree / relative(change['path'])
        if path.is_symlink() or digest(path) != change['before_sha256']:
            raise ValueError('Sanitizer preimage changed: ' + change['path'])
        if change['after'] is None:
            path.unlink()
        else:
            path.write_text(change['after'])


def audit_clean_tree(tree: Path, recipe: dict) -> dict:
    """Content and path scan before adding the public interface, not just names.

    The canonical full-tree identity subsequently rejects added or renamed
    implementation copies even when all their model identifiers are changed.
    """
    files = strict_inventory(tree)
    # Match our identifiers as tokens. Upstream touch_sensor_sample_config_t
    # is unrelated and must not be treated as a sensor_sample name collision.
    product_names = r'\b(?:g_)?(?:gpio_waveform|sensor_sample)(?:_[a-z0-9_]+)?\b|\bCONFIG_SYSTEM_SENSOR_SAMPLE\b'
    pattern = re.compile('|'.join(re.escape(x) for x in recipe['models']) + '|' + product_names, re.I)
    byte_pattern = re.compile(pattern.pattern.encode(), re.I)
    hits = [name for name in files if pattern.search(name)
            or byte_pattern.search((tree / name).read_bytes())]
    if hits:
        raise ValueError('Residual device/family materials or product-name conflict: ' + ', '.join(hits[:20]))
    return files


def integrate_public(root: Path, tree: Path, spec: dict) -> None:
    device = spec['device_key']
    from benchmark.common.target.interfaces import install_interfaces
    install_interfaces(root, tree / 'nuttx', spec['target_profile'])
    if device in ('bme280', 'sht3x', 'dht20', 'isl29035', 'lps22hb', 'apds9960', 'mpu6050', 'lsm6dso', 'ccs811', 'bmp388', 'dps310', 'lsm303dlhc', 'bmm150'):
        integrate_i2c(root, tree, device, spec['configuration']['connection']['address_7bit'])
        return
    if device == 'dht11':
        integrate_dht11(root, tree)
        return
    if device in ('bme280_spi', 'adt7310'):
        integrate_spi(root, tree, device)
        return
    if device != 'bh1750':
        raise ValueError('Reviewed public integration not yet implemented: ' + device)
    public = root / 'benchmark/common/target/nuttx/public'
    build = tree / 'nuttx/drivers/sensors/Make.defs'
    with build.open('a') as stream:
        stream.write('\n# Sensor task submission; the file is supplied by the candidate.\n'
                     'CSRCS += bh1750.c\n')
    board = tree / 'nuttx' / BOARD
    text = board.read_text()
    text = text.replace('#include "nrf52840-dk.h"',
                        '#include "nrf52840-dk.h"\n#include "nrf52_i2c.h"\n'
                        '#include <errno.h>\n#include <nuttx/sensors/bh1750.h>', 1)
    needle = 'int nrf52_bringup(void)\n{\n  int ret;'
    if text.count(needle) != 1:
        raise ValueError('Board integration anchor changed')
    text = text.replace(needle, needle + '\n  struct i2c_master_s *sensor_bus;\n\n'
                        '  sensor_bus = nrf52_i2cbus_initialize(0);\n'
                        '  if (sensor_bus == NULL)\n    {\n      return -ENODEV;\n    }\n'
                        '  ret = bh1750_register(sensor_bus, 0x23, 0);\n'
                        '  if (ret < 0)\n    {\n'
                        '      nrf52_i2cbus_uninitialize(sensor_bus);\n'
                        '      return ret;\n    }\n', 1)
    board.write_text(text)
    shutil.copytree(root / 'benchmark/common/applications/nuttx/template', tree / 'apps/system/sensor_sample')



def replace_once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise ValueError('Public integration anchor changed: ' + before)
    return text.replace(before, after, 1)


def integrate_spi(root: Path, tree: Path, device: str) -> None:
    public = root / 'benchmark/common/target/nuttx/public'
    build = tree / 'nuttx/drivers/sensors/Make.defs'
    with build.open('a') as stream:
        stream.write('\n# Sensor candidate submission.\nCSRCS += ' + device + '.c\n')
    board = tree / 'nuttx' / BOARD
    text = replace_once(board.read_text(), '#include "nrf52840-dk.h"',
                        '#include "nrf52840-dk.h"\n#include "nrf52_spi.h"\n'
                        '#include <errno.h>\n#include <nuttx/sensors/bme280_spi.h>')
    needle = 'int nrf52_bringup(void)\n{\n  int ret;'
    text = replace_once(text, needle, needle + '\n  struct spi_dev_s *sensor_bus;\n\n'
                        '  sensor_bus = nrf52_spibus_initialize(0);\n'
                        '  if (sensor_bus == NULL)\n    {\n      return -ENODEV;\n    }\n'
                        '  ret = bme280_spi_register(sensor_bus, SPIDEV_BAROMETER(0), 0);\n'
                        '  if (ret < 0)\n    {\n      return ret;\n    }\n')
    if device != 'bme280_spi':
        text = text.replace('bme280_spi', '' + device).replace('SPIDEV_BAROMETER', 'SPIDEV_TEMPERATURE')
    board.write_text(text)
    spi = board.with_name('nrf52_spi.c')
    text = replace_once(spi.read_text(), '#ifdef CONFIG_NRF52_SPI_MASTER',
                        '#define GPIO_SENSOR_CS (GPIO_OUTPUT | GPIO_VALUE_ONE | GPIO_PORT1 | GPIO_PIN(4))\n\n'
                        '#ifdef CONFIG_NRF52_SPI_MASTER')
    needle = 'void nrf52_spidev_initialize(void)\n{'
    text = replace_once(text, needle, needle + '\n  nrf52_gpio_config(GPIO_SENSOR_CS);\n'
                        '  nrf52_gpio_write(GPIO_SENSOR_CS, true);')
    needle = 'void nrf52_spi0select(struct spi_dev_s *dev, uint32_t devid,\n                      bool selected)\n{'
    text = replace_once(text, needle, needle + '\n  if (devid == SPIDEV_BAROMETER(0))\n'
                        '    {\n      nrf52_gpio_write(GPIO_SENSOR_CS, !selected);\n      return;\n    }\n')
    needle = 'uint8_t nrf52_spi0status(struct spi_dev_s *dev, uint32_t devid)\n{'
    text = replace_once(text, needle, needle + '\n  if (devid == SPIDEV_BAROMETER(0))\n'
                        '    {\n      return SPI_STATUS_PRESENT;\n    }\n')
    if device != 'bme280_spi':
        text = text.replace('SPIDEV_BAROMETER', 'SPIDEV_TEMPERATURE')
    spi.write_text(text)
    shutil.copytree(root / 'benchmark/common/applications/nuttx/template', tree / 'apps/system/sensor_sample')
    shutil.copyfile(root / 'benchmark/common/applications/nuttx' / (device + '.c'), tree / 'apps/system/sensor_sample/sensor_sample_main.c')



def integrate_dht11(root: Path, tree: Path) -> None:
    public = root / 'benchmark/common/target/nuttx/public'
    board = tree / 'nuttx' / BOARD
    with (tree / 'nuttx/drivers/sensors/Make.defs').open('a') as stream:
        stream.write('\n# Sensor candidate submission.\nCSRCS += dht11.c\n')
    with (board.parent / 'Make.defs').open('a') as stream:
        stream.write('\n# Generic digital waveform resource.\nCSRCS += gpio_waveform.c\n')
    text = replace_once(board.read_text(), '#include "nrf52840-dk.h"',
                        '#include "nrf52840-dk.h"\n#include <nuttx/sensors/dht11.h>')
    needle = 'int nrf52_bringup(void)\n{\n  int ret;'
    text = replace_once(text, needle, needle + '\n  ret = gpio_waveform_drive_low(false);\n'
                        '  if (ret < 0)\n    {\n      return ret;\n    }\n'
                        '  ret = dht11_register(0);\n'
                        '  if (ret < 0)\n    {\n      return ret;\n    }\n')
    board.write_text(text)
    shutil.copytree(root / 'benchmark/common/applications/nuttx/template', tree / 'apps/system/sensor_sample')
    shutil.copyfile(root / 'benchmark/common/applications/nuttx/dht11.c', tree / 'apps/system/sensor_sample/sensor_sample_main.c')



def integrate_i2c(root: Path, tree: Path, device: str, address: int) -> None:
    public = root / 'benchmark/common/target/nuttx/public'
    with (tree / 'nuttx/drivers/sensors/Make.defs').open('a') as stream:
        stream.write('\n# Sensor candidate submission.\nCSRCS += ' + device + '.c\n')
    board = tree / 'nuttx' / BOARD
    text = replace_once(board.read_text(), '#include "nrf52840-dk.h"',
                        '#include "nrf52840-dk.h"\n#include "nrf52_i2c.h"\n'
                        '#include <errno.h>\n#include <nuttx/sensors/bme280.h>')
    needle = 'int nrf52_bringup(void)\n{\n  int ret;'
    text = replace_once(text, needle, needle + '\n  struct i2c_master_s *sensor_bus;\n'
                        '  sensor_bus = nrf52_i2cbus_initialize(0);\n'
                        '  if (sensor_bus == NULL)\n    {\n      return -ENODEV;\n    }\n'
                        '  ret = bme280_register(sensor_bus, 0x76, 0);\n'
                        '  if (ret < 0)\n    {\n'
                        '      nrf52_i2cbus_uninitialize(sensor_bus);\n      return ret;\n    }\n')
    text = text.replace('bme280', '' + device).replace('0x76', f'0x{address:02x}')
    board.write_text(text)
    shutil.copytree(root / 'benchmark/common/applications/nuttx/template', tree / 'apps/system/sensor_sample')
    shutil.copyfile(root / 'benchmark/common/applications/nuttx' / (device + '.c'), tree / 'apps/system/sensor_sample/sensor_sample_main.c')


def integrate_platform(root: Path, tree: Path, spec: dict) -> dict:
    if spec['bus'] != 'i2c':
        return {}
    record_path = root / 'benchmark/common/target/nuttx/platform/i2c.json'
    record = json.loads(record_path.read_text())
    destination = tree / relative(record['target_path'])
    source = root / relative(record['replacement'])
    if (destination.is_symlink() or source.is_symlink()
            or digest(destination) != record['before_sha256']
            or digest(source) != record['after_sha256']):
        raise ValueError('Generic I2C platform derivative identity changed')
    shutil.copyfile(source, destination)
    return {'i2c': {'record_sha256': digest(record_path),
                    'source_sha256': record['before_sha256'],
                    'derived_sha256': record['after_sha256']}}

