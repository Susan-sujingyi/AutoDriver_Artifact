def mutations() -> dict:
    return {
        'wrong_humidity_unit': ('rh*100.0f/1048576.0f', 'rh*10000.0f/1048576.0f'),
        'wrong_temperature_offset': ('t*200.0f/1048576.0f-50.0f', 't*200.0f/1048576.0f'),
        'shared_nibble_shift': ('(data[3]&15)<<16', '(data[3]&15)<<12'),
        'crc_ignored': ('crc(data)!=data[6]', 'false'),
        'busy_ignored': ('if (!(data[0]&0x80)) break;', 'if (true) break;'),
        'startup_too_short': ('timestamp()+100000', 'timestamp()+50000'),
        'recovery_read_too_early': ('timestamp()+5000', 'timestamp()+1000'),
        'recovery_pair_lost': ('data[0]=0xb0|registers[i];', 'data[0]=0xb0|registers[i];data[1]=0;'),
        'calibration_not_verified': ('if ((status&0x18)!=0x18) return -EIO;', 'if (false) return -EIO;'),
        'deadline_exceeded': ('timestamp()+250000', 'timestamp()+350000'),
        'conflicting_retrigger': ('if (!(data[0]&0x80)) {', 'if (true) {'),
        'swallowed_transport_error': ('return ret<0 ? ret : 0;', 'return 0;'),
        'device_lock_leaked': ('done:\n  nxmutex_unlock(&dev->lock);', 'done:;'),
        'registration_cleanup_missing': ('while (i) sensor_unregister(&dev->nodes[--i].lower,devno);', 'i=0;'),
    }
