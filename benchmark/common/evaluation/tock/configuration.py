"""Render public task parameters for hidden models, without source rewriting."""


def render_spi(requirements):
    spi = requirements["connection"]["spi"]
    mode, rate = spi["mode"], spi["max_frequency_hz"]
    if type(mode) is not int or mode not in range(4):
        raise ValueError("Invalid task SPI mode")
    if type(rate) is not int or not 0 < rate <= 0xffffffff:
        raise ValueError("Invalid task SPI maximum frequency")
    if (spi["word_bits"] != 8 or spi["bit_order"].lower() != "msb_first"
            or spi["chip_select"] != "active_low"):
        raise ValueError("Unsupported task SPI framing for frozen SpiMasterDevice")
    polarity = "IdleHigh" if mode & 2 else "IdleLow"
    phase = "SampleTrailing" if mode & 1 else "SampleLeading"
    return f'''// Generated from the frozen task's structured connection requirements.
use kernel::hil::spi::{{ClockPhase, ClockPolarity}};
pub const SPI_MODE: u8 = {mode};
pub const SPI_MAX_HZ: u32 = {rate};
pub const SPI_POLARITY: ClockPolarity = ClockPolarity::{polarity};
pub const SPI_PHASE: ClockPhase = ClockPhase::{phase};
pub fn validate_spi(p: ClockPolarity, h: ClockPhase, rate: u32) {{
    assert!(p == SPI_POLARITY && h == SPI_PHASE,
            "CONFIG: required SPI mode {{}}", SPI_MODE);
    assert!(rate > 0 && rate <= SPI_MAX_HZ,
            "CONFIG: SPI frequency {{}} exceeds task maximum {{}}", rate, SPI_MAX_HZ);
}}
'''
