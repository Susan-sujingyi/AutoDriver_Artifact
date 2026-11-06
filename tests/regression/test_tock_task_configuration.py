import copy
import unittest

from benchmark.common.api import resolve_task
from benchmark.common.evaluation.tock.configuration import render_spi


class TockTaskConfiguration(unittest.TestCase):
    def test_all_frozen_spi_variants_render_their_own_mode(self):
        for sensor in ("adt7310", "bme280"):
            for source, mode in (("riot", 0), ("zephyr", 3)):
                task = resolve_task(f"{source}-tock-{sensor}-spi-mode{mode}")
                text = render_spi(task.requirements)
                self.assertIn(f"SPI_MODE: u8 = {mode};", text)
                self.assertIn("IdleHigh" if mode == 3 else "IdleLow", text)
                self.assertIn("SampleTrailing" if mode == 3 else "SampleLeading", text)

    def test_invalid_or_unimplemented_framing_is_rejected(self):
        original = resolve_task("riot-tock-adt7310-spi-mode0").requirements
        for key, value in (("mode", 4), ("mode", True), ("mode", "0"),
                           ("max_frequency_hz", 0), ("max_frequency_hz", -1),
                           ("word_bits", 16), ("bit_order", "lsb_first"),
                           ("chip_select", "active_high")):
            with self.subTest(key=key, value=value):
                spec = copy.deepcopy(original)
                spec["connection"]["spi"][key] = value
                with self.assertRaises(ValueError): render_spi(spec)

    def test_mode_and_frequency_are_independent_parameters(self):
        original = resolve_task("riot-tock-adt7310-spi-mode0").requirements
        for mode in range(4):
            spec = copy.deepcopy(original)
            spec["connection"]["spi"].update(mode=mode, max_frequency_hz=500000)
            text = render_spi(spec)
            self.assertIn("SPI_MAX_HZ: u32 = 500000;", text)
            self.assertIn("IdleHigh" if mode & 2 else "IdleLow", text)
            self.assertIn("SampleTrailing" if mode & 1 else "SampleLeading", text)
