"""Host-only controls for separately scored SPI variants; never agent materials."""
def once(source, old, new):
    if source.count(old)!=1:raise ValueError('Control anchor missing/ambiguous: '+old)
    return source.replace(old,new,1)

def controls(chip, s, mode=3):
    if mode not in (0, 3):raise ValueError('Unsupported SPI control mode')
    polarity='IdleLow' if mode==0 else 'IdleHigh'
    opposite='IdleHigh' if mode==0 else 'IdleLow'
    result={'reference':(s,'pass'),
        'slower_500khz':(once(s,'1_000_000)?;','500_000)?;'),'pass'),
        'wrong_mode':(once(s,'ClockPolarity::'+polarity,'ClockPolarity::'+opposite),'fail'),
        'exceeds_clock_limit':(once(s,'1_000_000)?;','2_000_000)?;'),'fail')}
    if chip=='bmp388':
        def interface(value):
            x=once(s,'enum State { Off,','enum State { Interface, Off,')
            return once(x,'State::Mode=>self.read(buffer,0x02,1,State::InitError),',f'State::Mode=>self.write(buffer,0x1a,{value},State::Interface),\n            State::Interface=>self.read(buffer,0x02,1,State::InitError),')
        result.update({
            'irrelevant_i2c_watchdog':(interface(6),'pass'),
            'wrong_three_wire':(interface(1),'fail'),
            'rounded_numeric':(s.replace('(t*100.0) as i64','(t*100.0+if t>=0.0{0.5}else{-0.5}) as i64').replace('(p/100.0) as i64','(p/100.0+0.5) as i64'),'pass'),
            'no_id_probe':(once(s,'State::ResetWait=>self.read(buffer,0x00,1,State::Identity),','State::ResetWait=>self.read(buffer,0x31,21,State::Calibration),'),'pass'),
            'temperature_wrong_unit':(once(s,'(t*100.0) as i64','t as i64'),'fail'),
            'pressure_wrong_unit':(once(s,'(p/100.0) as i64','p as i64'),'fail'),
            'wrong_osr':(once(s,'self.write(buffer,0x1c,0x02,State::Osr)','self.write(buffer,0x1c,0x00,State::Osr)'),'fail'),
            'dummy_not_discarded':(once(s,'buffer.copy_within(2..n+2,0)','buffer.copy_within(1..n+1,0)'),'not_completed'),
            'sample_wrong_offset':(once(s,'buffer.copy_within(2..n+2,0)','buffer.copy_within(if self.state.get()==State::Sample{1..n+1}else{2..n+2},0)'),'fail'),
            'sample_short_clock':(once(s,'len+2};','len+if next==State::Sample{1}else{2}};'),'not_completed'),
            'wrong_read_flag':(once(s,'reg|0x80','reg&0x7f'),'fail'),
            'duplicate_callback':(once(s,'c.callback(value.map(|v|v as i32));','c.callback(value.map(|v|v as i32));c.callback(value.map(|v|v as i32));'),'fail'),
            'missing_callback':(once(s,'c.callback(value.map(|v|v as i32));','let _=(c,value);'),'not_completed'),
        })
    elif chip=='bme280':
        result.update({
            'wrong_three_wire':(once(s,'self.write_register(buffer, 0xf5, 0, State::Config)','self.write_register(buffer, 0xf5, 1, State::Config)'),'fail'),
            'polling_interval_change':(once(s,'State::ConversionWait, 5);','State::ConversionWait, 10);'),'pass'),
            'pressure_wrong_unit':(once(s,'let hpa = pressure / 25600;','let hpa = pressure / 256;'),'fail'),
            'humidity_byte_missing':(once(s,'self.read_register(buffer, 0xf7, 8, State::Sample)','self.read_register(buffer, 0xf7, 7, State::Sample)'),'fail'),
            'wrong_osr':(once(s,'self.write_register(buffer, 0xf4, 0x35, State::Trigger)','self.write_register(buffer, 0xf4, 0x25, State::Trigger)'),'fail'),
            'sample_wrong_offset':(once(s,'buffer.copy_within(1..n+1,0)','if self.state.get()!=State::Sample{buffer.copy_within(1..n+1,0);}'),'fail'),
            'wrong_read_flag':(once(s,'reg|0x80','reg&0x7f'),'fail'),
            'duplicate_callback':(once(s,'client.callback(Ok(temperature));','client.callback(Ok(temperature));client.callback(Ok(temperature));'),'fail'),
            'missing_callback':(once(s,'client.callback(Ok(temperature));','let _=(client,temperature);'),'not_completed'),
        })
    else:
        result.update({
            'disable_unused_i2c':(once(s,'(0x13,0x08)','(0x13,0x0c)'),'pass'),
            'wrong_three_wire':(once(s,'(0x12,0x44)','(0x12,0x4c)'),'fail'),
            'signed_extension':(once(s,'xyz[i]=(value as u32) as usize;','xyz[i]=value as usize;'),'pass'),
            'float32_numeric':(once(s,'(raw*61*9_806_650/1_000_000) as i32','(raw as f32*61.0*9_806_650.0/1_000_000.0) as i32'),'pass'),
            'wrong_unit':(once(s,'(raw*61*9_806_650/1_000_000) as i32','raw as i32'),'fail'),
            'wrong_byte_order':(s.replace('i16::from_le_bytes','i16::from_be_bytes'),'fail'),
            'wrong_range':(once(s,'(0x10,0x40)','(0x10,0x48)'),'fail'),
            'sample_wrong_offset':(once(s,'b.copy_within(1..n+1,0)','if self.state.get()!=State::Data{b.copy_within(1..n+1,0);}'),'fail'),
            'wrong_read_flag':(once(s,'tx[0]|0x80','tx[0]&0x7f'),'model_unsupported'),
            'disable_auto_increment':(once(s,'(0x12,0x44)','(0x12,0x40)'),'fail'),
            'duplicate_callback':(once(s,'c.callback(xyz[0],xyz[1],xyz[2]);','c.callback(xyz[0],xyz[1],xyz[2]);c.callback(xyz[0],xyz[1],xyz[2]);'),'fail'),
            'missing_callback':(once(s,'c.callback(xyz[0],xyz[1],xyz[2]);','let _=(c,xyz);'),'not_completed'),
        })
    return result
