"""SIM reports bind the actual task configuration and vector-generation code."""
import unittest
from unittest.mock import patch

from benchmark.common.api import ROOT, resolve_task, digest
from benchmark.common.evaluation.nuttx.sim.adapter import implementation_files, model_directory, render_model


class SimIdentity(unittest.TestCase):
    def test_shared_sensor_device_is_bound_for_both_bus_tasks(self):
        tasks = [resolve_task(name) for name in
                 ('zephyr-nuttx-bme280-i2c', 'zephyr-nuttx-bme280-spi-mode3')]
        self.assertEqual(model_directory(tasks[0]), model_directory(tasks[1]))
        shared = 'benchmark/bme280/evaluation/nuttx/sim/device.c'
        for task in tasks:
            files = implementation_files(task)
            self.assertEqual(files[shared], digest(ROOT / shared))
            with patch('benchmark.common.evaluation.nuttx.sim.adapter.digest',
                       side_effect=lambda p: 'modified' if p == ROOT / shared else digest(p)):
                self.assertNotEqual(files, implementation_files(task))

    def test_shared_model_keeps_scenarios_equal_and_transport_specific(self):
        i2c = render_model(resolve_task('zephyr-nuttx-bme280-i2c'))
        spi = render_model(resolve_task('zephyr-nuttx-bme280-spi-mode3'))
        self.assertEqual(i2c[1:], spi[1:])
        self.assertEqual(i2c[0]['device.c'], spi[0]['device.c'])
        self.assertIn('#define BENCH_SPI 0', i2c[0]['scenario.h'])
        self.assertIn('#define BENCH_SPI_MODE SPIDEV_MODE3', spi[0]['scenario.h'])
        self.assertNotEqual(i2c[0]['scenario.h'], spi[0]['scenario.h'])

    def test_bmp388_transitive_renderer_and_public_contract_are_bound(self):
        task = resolve_task('zephyr-nuttx-bmp388-i2c')
        baseline = implementation_files(task)
        expected = ['benchmark/bmp388/evaluation/c_vectors.py',
                    'benchmark/bmp388/source/zephyr/configuration.yaml',
                    'benchmark/bmp388/target/nuttx/i2c/board-support/driver_api.h',
                    'benchmark/bmp388/tasks/zephyr-nuttx-i2c/task.yaml',
                    'third_party/sources.lock']
        for name in expected:
            self.assertEqual(baseline[name], digest(ROOT / name))
        renderer = ROOT / expected[0]
        def revised(path):
            return 'changed-vector-renderer' if path == renderer else digest(path)
        # No mutation of frozen materials is needed to verify drift detection.
        with patch('benchmark.common.evaluation.nuttx.sim.adapter.digest', revised):
            changed = implementation_files(task)
        self.assertNotEqual(baseline, changed)


if __name__ == '__main__':
    unittest.main()
