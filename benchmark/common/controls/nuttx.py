"""Hidden source variants for validating the frozen NuttX evaluator.

These fixtures are benchmark materials, never method inputs or driver scoring
rules. Scenario assertions remain exclusively in the independent evaluator.
"""
def adt_source(text, control):
    """Explicit maintenance fixtures; never rewrite a submitted candidate."""
    if control == "reference":
        return text
    substitutions = {
        "legal_split_spi": (
            "SPI_EXCHANGE(dev->spi,tx,rx,n+1);",
            "SPI_SEND(dev->spi,command);\n"
            "  if (data) SPI_RECVBLOCK(dev->spi,rx+1,n);\n"
            "  else SPI_SNDBLOCK(dev->spi,tx+1,n);"),
        "wrong_scale": ("raw/128.0f", "raw/256.0f"),
        "wrong_mode": ("SPIDEV_MODE3", "SPIDEV_MODE0"),
    }
    before, after = substitutions[control]
    if text.count(before) != 1:
        raise ValueError("Control fixture no longer matches its reviewed reference")
    return text.replace(before, after)


import re

def bounded_transport_retry(source):
    pattern = re.compile(r'(?P<line>(?:int\s+)?(?P<var>ret|result)\s*=\s*(?P<call>(?:I2C_TRANSFER|i2c_write|i2c_read)\([^;]+\)|SPI_LOCK\([^;]+,\s*true\));)')
    count = 0
    def rewrite(match):
        nonlocal count
        count += 1
        return match['line'] + '\n  if (' + match['var'] + ' < 0) ' + match['var'] + ' = ' + match['call'] + ';'
    result = pattern.sub(rewrite, source)
    if count == 0:
        raise ValueError("No reviewed transport call matched the bounded-retry control")
    return result


def swallowed_transport_error(source):
    """Deliberately report transfer success after a failed physical operation."""
    pattern = re.compile(r'(?P<line>(?:int\s+)?(?P<var>ret|result)\s*=\s*(?P<call>(?:I2C_TRANSFER|i2c_write|i2c_read)\([^;]+\));)')
    result, count = pattern.subn(lambda m: m['line'] + '\n  if (' + m['var'] + ' < 0) ' + m['var'] + ' = 0;', source)
    if not count:
        raise ValueError("Transport-swallow fixture anchor changed")
    return result


def bme280_factory_validation(text,leak=False):
 anchor='  nxmutex_init(&device->lock);\n  for (i = 0; i < 3; i++)'
 assert text.count(anchor)==1
 block='''  nxmutex_init(&device->lock);
  /* Legal eager calibration validation, without triggering acquisition. */
  result = initialize(device);
  if (result == 0 && device->calibration.p1 == 0)
    {
      result = -EINVAL;
'''
 if leak:
  block+='''      /* Deliberately faulty: leave a registered node on factory failure. */
      device->nodes[0].owner = device;
      device->nodes[0].lower.type = SENSOR_TYPE_AMBIENT_TEMPERATURE;
      device->nodes[0].lower.ops = &operations;
      sensor_register(&device->nodes[0].lower, devno);
      return result;
'''
 block+='''    }
  if (result < 0)
    {
      nxmutex_destroy(&device->lock);
      kmm_free(device);
      return result;
    }
  for (i = 0; i < 3; i++)'''
 return text.replace(anchor,block)


def control_source(task, text, variant):
    """Reviewed maintenance fixtures; canonical references are never edited."""
    if variant == "reference":
        return text
    if task.sensor == "bme280" and variant in ("legal_eager_calibration_validation", "fault_eager_validation_leaks_node"):
        return bme280_factory_validation(text, variant == "fault_eager_validation_leaks_node")
    if variant == "legal_bounded_transport_retry":
        return bounded_transport_retry(text)
    if variant == "fault_swallowed_transport_error":
        return swallowed_transport_error(text)
    if task.sensor == "bh1750" and variant == "legal_native_one_shot_lifecycle":
        # Native open/close controls the subscriber lifecycle. This public ABI
        # does not prescribe a second guard for direct lower-fetch after close.
        before = "  if (!dev->active)\n    {\n      ret = -EACCES;\n      goto out;\n    }\n\n"
        if text.count(before) != 1:
            raise ValueError("BH1750 lifecycle control anchor changed")
        return text.replace(before, "", 1)
    if task.sensor == "adt7310":
        return adt_source(text, variant)
    if task.sensor == "dht11" and variant == "scheduler_sleep":
        substitutions = [('int result = nxsig_usleep((useconds_t)(deadline-now));',
                          'nxsched_usleep((useconds_t)(deadline-now));\n      int result = 0;')]
        text = '#include <nuttx/sched.h>\n' + text
    elif task.sensor == "ccs811" and variant == "legal_no_id":
        substitutions = [('  if((ret=transfer(dev,0x20,&value,1,true,end))<0)goto fail;\n'
                          '  if(value!=0x81){ret=-ENODEV;goto fail;}\n', '')]
    elif task.sensor == "bh1750" and variant == "legal_explicit_config_busywait":
        text = '#include <nuttx/arch.h>\n' + text
        substitutions = [
            ('ret = i2c_write(dev->bus, &dev->config, &command, 1);',
             'uint8_t commands[] = {0x01, 0x07, 0x65, 0x42};\n'
             '  for (unsigned i = 0; i < sizeof(commands); i++)\n'
             '    { ret = i2c_write(dev->bus, &dev->config, &commands[i], 1);\n'
             '      if (ret < 0) goto out; }\n'
             '  ret = i2c_write(dev->bus, &dev->config, &command, 1);'),
            ('+ 180000;', '+ 300000;'),
            ('ret = nxsig_usleep((useconds_t)(deadline - now));',
             'up_udelay((useconds_t)(deadline - now)); ret = 0;'),
        ]
    elif task.sensor == "bh1750" and variant == "wrong_scale":
        substitutions = [('/ 1.2f;', '/ 2.4f;')]
    elif task.sensor == "bh1750" and variant == "premature_read":
        substitutions = [('+ 180000;', '+ 120000;')]
    elif task.sensor == "sht3x" and variant == "ignore_crc":
        substitutions = [('if (crc(data+offset) != data[offset+2])', 'if (false)')]
    elif task.sensor == "sht3x" and variant == "legal_i2c_helpers":
        substitutions = [('int ret=I2C_TRANSFER(dev->bus,&msg,1);',
                          'struct i2c_config_s cfg={.frequency=msg.frequency, .address=msg.addr, .addrlen=7};\n'
                          '  int ret=read ? i2c_read(dev->bus,&cfg,data,length) :\n'
                          '                 i2c_write(dev->bus,&cfg,data,length);')]
    elif task.sensor == "bmp388" and variant == "legal_stable_power_no_extra_wait":
        substitutions = [('  if((ret=wait_until(sensor_get_timestamp()+2000))<0)goto fail;\n  for(;;){',
                          '  for(;;){')]
    elif task.sensor == "bmp388" and variant == "unsupported_early_reset_access":
        substitutions = [('value=0xb6;if((ret=transfer(dev,0x7e,&value,1,false,end))<0)goto fail;\n'
                          '  if((ret=wait_until(sensor_get_timestamp()+2000))<0)goto fail;',
                          'value=0xb6;if((ret=transfer(dev,0x7e,&value,1,false,end))<0)goto fail;')]
    elif task.sensor == "bmp388" and variant == "unsupported_sensor_clock":
        substitutions = [('  if((ret=transfer(dev,0x31,trim,21,true,end))<0)goto fail;',
                          '  { uint8_t clock[3]; if((ret=transfer(dev,0x0c,clock,3,true,end))<0)goto fail; }\n'
                          '  if((ret=transfer(dev,0x31,trim,21,true,end))<0)goto fail;')]
    elif task.sensor == "isl29035" and variant == "legal_two_bytes":
        substitutions = [('transfer(dev,0x02,data,1,true,deadline)',
                          'transfer(dev,0x02,data,2,true,deadline)')]
    elif task.sensor == "isl29035" and variant == "wrong_scale":
        substitutions = [('data[0]*4000.0f/256.0f', 'data[0]*4000.0f/65536.0f')]
    elif task.sensor == "lps22hb" and variant == "legal_order_no_id":
        substitutions = [
            ('{0x2b,0x2c,0x28,0x29,0x2a}', '{0x2c,0x2b,0x29,0x28,0x2a}'),
            ('  if ((ret=transfer(dev,0x0f,&value,true,end))<0) goto fail;\n'
             '  if (value!=0xb1) {ret=-ENODEV;goto fail;}\n', '')]
    elif task.sensor == "lps22hb" and variant == "wrong_scale":
        substitutions = [('raw/4096.0f', 'raw/2048.0f')]
    elif task.sensor == "mpu6050" and variant == "legal_full_burst_no_id":
        substitutions = [
            ('transfer(dev,0x3b,bytes,6,true,end)', 'transfer(dev,0x3b,bytes,14,true,end)'),
            ('  if((ret=transfer(dev,0x75,&value,1,true,end))<0)goto fail;\n'
             '  if(value!=0x68 && value!=0x70){ret=-ENODEV;goto fail;}\n', '')]
    elif task.sensor == "mpu6050" and variant == "wrong_scale":
        substitutions = [('raw*9.80665/16384.0', 'raw*9.80665/8192.0')]
    elif task.sensor == "dht20" and variant in ("legal_arch_delay", "ignore_crc_arch_delay"):
        text = '#include <nuttx/arch.h>\n' + text
        # The approved version names the remaining interval explicitly.
        # Keep the reviewed busy-delay control usable with both immutable
        # versions; all deadline admission checks remain in the new fixture.
        duration = 'duration' if 'int ret=nxsig_usleep(duration);' in text else 'end-now'
        substitutions = [(f'int ret=nxsig_usleep({duration});', f'up_udelay({duration}); int ret=0;')]
        if variant == "ignore_crc_arch_delay":
            substitutions += [('if (crc(data)!=data[6])', 'if (false)')]
    elif task.sensor == "lsm6dso" and variant == "legal_reverse_pairs_no_id":
        substitutions = [
            ('  uint8_t bytes[2]={reg,read ? 0 : *data};',
             '  if(read && reg==0x28 && count==6) {\n'
             '    const unsigned order[]={1,0,3,2,5,4};\n'
             '    for(unsigned i=0;i<6;i++) {int result=transfer(dev,reg+order[i],data+order[i],1,true,end); if(result<0)return result;}\n'
             '    return 0;\n  }\n'
             '  uint8_t bytes[2]={reg,read ? 0 : *data};'),
            ('  if((ret=transfer(dev,0x0f,&value,1,true,end))<0)goto fail;\n'
             '  if(value!=0x6c){ret=-ENODEV;goto fail;}\n','')]
    elif task.sensor == "lsm6dso" and variant == "wrong_scale":
        substitutions = [('raw*61.0*9.80665/1000000.0', 'raw*9.80665/16384.0')]
    elif task.sensor == "lsm303dlhc" and variant == "legal_reverse_bytes":
        substitutions = [('  uint8_t bytes[4]={reg};',
             '  if(read && reg==3 && count==6) {\n'
             '    for(int i=5;i>=0;i--) {int result=transfer(dev,reg+i,data+i,1,true,end);if(result<0)return result;}\n'
             '    return 0;\n  }\n  uint8_t bytes[4]={reg};')]
    elif task.sensor == "lsm303dlhc" and variant == "wrong_z_scale":
        substitutions = [('sample.z=z*100.0/980;', 'sample.z=z*100.0/1100;')]
    elif task.sensor == "bmm150" and variant == "legal_reverse_bytes_no_id":
        substitutions = [
            ('  uint8_t bytes[22]={reg};',
             '  if(read && count>1) {\n'
             '    for(int i=(int)count-1;i>=0;i--) {int r=transfer(dev,reg+i,data+i,1,true,end);if(r<0)return r;}\n'
             '    return 0;\n  }\n  uint8_t bytes[22]={reg};'),
            ('  if((ret=transfer(dev,0x40,&value,1,true,end))<0)goto fail;\n'
             '  if(value!=0x32){ret=-ENODEV;goto fail;}\n','')]
    elif task.sensor == "bmm150" and variant == "legal_nine_byte_burst":
        substitutions = [('uint8_t status,bytes[8];', 'uint8_t status,bytes[9];'),
                         ('transfer(dev,0x42,bytes,8,true,end)', 'transfer(dev,0x42,bytes,9,true,end)')]
    elif task.sensor == "bmm150" and variant == "wrong_z_width":
        substitutions = [('int z=signed_value(word(bytes+4)>>1,15);',
                          'int z=signed_value(word(bytes+4)>>1,13);')]
    elif task.sensor == "bmm150" and variant == "wrong_xyz1_mask":
        substitutions = [('dev->xyz1=word(trim+15)&0x7fff;', 'dev->xyz1=word(trim+15);')]
    elif task.sensor == "dps310" and variant == "legal_reverse_bytes_no_id":
        substitutions = [
            ('  uint8_t bytes[5]={reg};',
             '  if(read && count>1) {\n'
             '    for(int i=(int)count-1;i>=0;i--) {int r=transfer(dev,reg+i,data+i,1,true,end);if(r<0)return r;}\n'
             '    return 0;\n  }\n  uint8_t bytes[5]={reg};'),
            ('  if((ret=transfer(dev,0x0d,&value,1,true,end))<0)goto fail;\n'
             '  if(value!=0x10){ret=-ENODEV;goto fail;}\n','')]
    elif task.sensor == "dps310" and variant == "policy_omit_private_correction":
        substitutions = [('  for(unsigned i=0;i<5;i++){\n'
                          '    value=correction[i][1];if((ret=transfer(dev,correction[i][0],&value,1,false,end))<0)goto fail;\n'
                          '  }\n', '')]
    elif task.sensor == "dps310" and variant == "wrong_scale":
        substitutions = [('double t=raw_t/7864320.0;', 'double t=raw_t/1048576.0;')]
    elif task.sensor == "bme280" and variant == "legal_split_mode2_no_id":
        # Optional ID probing and one combined exchange are not required.
        substitutions = [
            ('  int result = transfer(device, 0xd0, &id, 1, true);\n'
             '  if (result < 0)\n    {\n      return result;\n    }\n\n'
             '  if (id != 0x60)\n    {\n      return -ENODEV;\n    }\n',
             '  int result;\n'),
            ('write_reg(device, 0xf4, 0x35)', 'write_reg(device, 0xf4, 0x36)')]
        if task.bus == "spi":
            substitutions += [('  SPI_EXCHANGE(device->spi, tx, rx, count + 1);',
                               '  SPI_EXCHANGE(device->spi, tx, rx, 1);\n'
                               '  SPI_EXCHANGE(device->spi, tx+1, rx+1, count);')]
        else:
            substitutions += [('  uint8_t tx[27];',
                '  if (read && count > 1) {\n'
                '    for (int i=(int)count-1;i>=0;i--) {int r=transfer(device,reg+i,data+i,1,true);if(r<0)return r;}\n'
                '    return 0;\n  }\n  uint8_t tx[27];')]
    elif task.sensor == "bme280" and variant == "wrong_scale":
        substitutions = [('return *fine / 5120.0;', 'return *fine / 10240.0;')]
    elif task.sensor == "bme280" and variant == "wrong_oversampling":
        substitutions = [('write_reg(device, 0xf4, 0x35)', 'write_reg(device, 0xf4, 0x31)')]
    elif task.sensor == "apds9960" and variant == "legal_no_id":
        substitutions = [('  if ((ret=wait_until(sensor_get_timestamp()+6000))<0 ||\n'
                          '      (ret=transfer(dev,0x92,&value,true,end))<0) goto fail;\n'
                          '  if (value!=0xab) {ret=-ENODEV;goto fail;}\n',
                          '  if ((ret=wait_until(sensor_get_timestamp()+6000))<0) goto fail;\n')]
    elif task.sensor == "apds9960" and variant == "wrong_scale":
        substitutions = [('sample.raw=value;', 'sample.raw=value/2;')]
    else:
        raise ValueError("Unknown development control for " + task.id)
    for before, after in substitutions:
        if text.count(before) != 1:
            raise ValueError("Reviewed control no longer matches reference: " + before)
        text = text.replace(before, after, 1)
    return text

