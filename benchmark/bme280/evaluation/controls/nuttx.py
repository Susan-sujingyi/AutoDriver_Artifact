"""Private SPI mode mismatch for the fixed task configuration."""
def mutations():
    return {'wrong_spi_mode': ('SPIDEV_MODE3','SPIDEV_MODE0')}
