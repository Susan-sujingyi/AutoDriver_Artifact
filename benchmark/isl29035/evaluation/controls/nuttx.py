def mutations() -> dict:
    return {
        'wrong_range_scale': ('data[0]*4000.0f/256.0f','data[0]*1000.0f/256.0f'),
        'wrong_adc_denominator': ('data[0]*4000.0f/256.0f','data[0]*4000.0f/255.0f'),
        'signed_adc': ('data[0]*4000.0f/256.0f','(int8_t)data[0]*4000.0f/256.0f'),
        'wrong_identity_mask': ('(id&0x38)!=0x28','(id&0x18)!=0x08'),
        'brownout_not_cleared': ('value=id&0x7f;','value=id;'),
        'wrong_resolution': ('value=0x09;','value=0x05;'),
        'wrong_mode': ('value=0xa0;','value=0x20;'),
        'unused_upper_byte_as_signal': ('transfer(dev,0x02,data,1,true,deadline)','transfer(dev,0x03,data,1,true,deadline)'),
        'integration_wait_omitted': ('timestamp()+2000','timestamp()+0'),
        'request_deadline_exceeded': ('timestamp()+2000','timestamp()+12000'),
        'swallowed_transport_error': ('return ret<0 ? ret : 0;','return 0;'),
        'device_lock_leaked': ('done:\n  nxmutex_unlock(&dev->lock);return ret;','done:\n  return ret;'),
        'failed_factory_allocation_leaked': ('nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;','nxmutex_destroy(&dev->lock);return ret;'),
    }
