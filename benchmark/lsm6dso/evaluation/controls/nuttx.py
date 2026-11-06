"""Private compiling faults; every result is executed, not string-scored."""
def mutations():
    return {
        'unsigned_axes': ('int16_t raw=', 'uint16_t raw='),
        'big_endian_axes': ('((uint16_t)bytes[2*i+1]<<8)|bytes[2*i]', '((uint16_t)bytes[2*i]<<8)|bytes[2*i+1]'),
        'mpu_sensitivity': ('*61.0*9.80665/1000000.0', '*9.80665/16384.0'),
        'wrong_scale_4g': ('*61.0*9.80665', '*122.0*9.80665'),
        'unit_micrometers': ('*61.0*9.80665/1000000.0', '*61.0*9.80665'),
        'unit_mg': ('*61.0*9.80665/1000000.0', '*0.061'),
        'axis_swap': ('sample.x=xyz[0];sample.y=xyz[1];', 'sample.x=xyz[1];sample.y=xyz[0];'),
        'axis_sign_flip': ('sample.z=xyz[2];', 'sample.z=-xyz[2];'),
        'zero_invalid': ('xyz[i]=(double)raw', 'if(raw==0){ret=-EIO;goto done;}xyz[i]=(double)raw'),
        'minimum_invalid': ('xyz[i]=(double)raw', 'if(raw==INT16_MIN){ret=-EIO;goto done;}xyz[i]=(double)raw'),
        'unsettled_first': ('{0x13,8}', '{0x13,0}'),
        'range_4g': ('{0x10,0x40}', '{0x10,0x48}'),
        'wrong_odr': ('{0x10,0x40}', '{0x10,0x30}'),
        'low_power': ('{0x15,0}', '{0x15,0x10}'),
        'gyro_enabled': ('{0x11,0}', '{0x11,0x40}'),
        'fifo_enabled': ('{0x0a,0}', '{0x0a,1}'),
        'bdu_disabled': ('{0x12,0x44}', '{0x12,4}'),
        'increment_disabled': ('{0x12,0x44}', '{0x12,0x40}'),
        'identity_ignored': ('if(value!=0x6c)', 'if(false)'),
        'wrong_bank': ('value=0;if((ret=transfer(dev,0x01', 'value=0x80;if((ret=transfer(dev,0x01'),
        'readiness_ignored': ('if(status&1)break;', 'if(true)break;'),
        'wrong_ready_bit': ('if(status&1)break;', 'if(status&2)break;'),
        'deadline_exceeded': ('    /* Poll transfers advance time;', '    nxsig_usleep(100000);\n    /* Poll transfers advance time;'),
        'swallowed_bus_error': ('return ret<0 ? ret : 0;', 'return 0;'),
        'timestamp_zero': ('sample.timestamp=sensor_get_timestamp();', 'sample.timestamp=0;'),
        'fabricated_temperature': ('sample.temperature=NAN;', 'sample.temperature=0;'),
        'wrong_sensor_type': ('dev->lower.type=SENSOR_TYPE_ACCELEROMETER;', 'dev->lower.type=2;'),
        'lock_leaked': ('done:\n  nxmutex_unlock(&dev->lock);return ret;', 'done:\n  return ret;'),
        'allocation_leaked': ('nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;', 'nxmutex_destroy(&dev->lock);return ret;'),
        'incoherent_axes': (
            '  if((ret=transfer(dev,0x28,bytes,6,true,end))<0)goto done;\n  memset(&sample',
            '  for(unsigned axis=0;axis<3;axis++){\n'
            '    if((ret=transfer(dev,0x28+2*axis,bytes+2*axis,2,true,end))<0)goto done;\n'
            '    if(axis<2)nxsig_usleep(10000);\n  }\n  memset(&sample'),
    }
