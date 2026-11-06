def mutations() -> dict:
    return {
        'pressure_in_kpa': ('raw/4096.0f', 'raw/40960.0f'),
        'temperature_in_centic': ('raw/100.0f', 'raw/1.0f'),
        'unsigned_temperature': ('int16_t raw=(uint16_t)bytes[3]', 'uint16_t raw=(uint16_t)bytes[3]'),
        'temperature_bytes_reversed': ('(uint16_t)bytes[3]|((uint16_t)bytes[4]<<8)', '(uint16_t)bytes[4]|((uint16_t)bytes[3]<<8)'),
        'wrong_identity': ('value!=0xb1', 'value!=0xbd'),
        'bdu_disabled': ('value=0x12;', 'value=0x10;'),
        'low_pass_enabled': ('value=0x12;', 'value=0x1a;'),
        'readiness_ignored': ('if ((status&3)==3) break;', 'if (true) break;'),
        'one_ready_bit_suffices': ('if ((status&3)==3) break;', 'if (status&3) break;'),
        'latch_released_before_temperature': ('{0x2b,0x2c,0x28,0x29,0x2a}', '{0x28,0x29,0x2a,0x2b,0x2c}'),
        'partial_read_recovery_omitted': ('if (dev->partial) {', 'if (false) {'),
        'native_sleep_margin_missing': ('if (now+30000>=end)', 'if (now+10000>=end)'),
        'deadline_exceeded': ('timestamp()+2200000', 'timestamp()+3200000'),
        'swallowed_transport_error': ('return ret<0 ? ret : 0;', 'return 0;'),
        'device_lock_leaked': ('done:\n  nxmutex_unlock(&dev->lock);return ret;', 'done:\n  return ret;'),
        'registration_cleanup_missing': ('while (i) sensor_unregister(&dev->nodes[--i].lower,devno);', 'i=0;'),
    }
