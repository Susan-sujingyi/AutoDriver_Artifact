def mutations() -> dict:
    return {
        'wrong_humidity_unit': ('100.0f*raw/65535.0f', '10000.0f*raw/65535.0f'),
        'signed_raw_signal': ('uint16_t raw;', 'int16_t raw;'),
        'byte_order_reversed': ('((uint16_t)data[offset]<<8)|data[offset+1]',
                                '((uint16_t)data[offset+1]<<8)|data[offset]'),
        'crc_ignored': ('crc(data+offset) != data[offset+2]', 'false'),
        'wrong_measurement_command': ('{0x24,0x00}', '{0x24,0x16}'),
        'deadline_exceeded': ('timestamp()+100000', 'timestamp()+200000'),
        'swallowed_bus_error': ('return ret < 0 ? ret : 0;', 'return 0;'),
        'device_lock_leaked': ('done:\n  nxmutex_unlock(&dev->lock);', 'done:;'),
        'registration_cleanup_missing':
            ('while (i) sensor_unregister(&dev->nodes[--i].lower,devno);', 'i=0;'),
    }
