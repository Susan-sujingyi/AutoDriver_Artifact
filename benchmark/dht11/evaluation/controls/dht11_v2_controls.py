"""Host-only DHT11 control generators."""
def candidates(source):
    yield 'reference',source,'pass',None
    for name,old,new in (
        ('threshold_40','THRESHOLD_US: u32 = 50','THRESHOLD_US: u32 = 40'),
        ('threshold_60','THRESHOLD_US: u32 = 50','THRESHOLD_US: u32 = 60'),
        ('start_21ms','START_US: u32 = 19000','START_US: u32 = 21000'),
        ('near_minimum_acquisition','GAP_US: u32 = 1100000','GAP_US: u32 = 980000'),
        ('slower_acquisition','GAP_US: u32 = 1100000','GAP_US: u32 = 1500000'),
    ):
        assert source.count(old)==1;yield name,source.replace(old,new),'pass',None
    for name,old,new in (
        ('wrong_temperature_unit','Ok(b[2]as i32*100)','Ok(b[2]as i32)'),
        ('wrong_humidity_unit','c.callback(b[0]as usize*100)','c.callback(b[0]as usize)'),
        ('swapped_temperature','Ok(b[2]as i32*100)','Ok(b[0]as i32*100)'),
        ('short_start','START_US: u32 = 19000','START_US: u32 = 1000'),
        ('no_cooldown','GAP_US: u32 = 1100000','GAP_US: u32 = 0'),
        ('wrong_bit_threshold','THRESHOLD_US: u32 = 50','THRESHOLD_US: u32 = 90'),
        ('duplicate_temperature','c.callback(if valid{Ok(b[2]as i32*100)}else{Err(ErrorCode::FAIL)});','c.callback(if valid{Ok(b[2]as i32*100)}else{Err(ErrorCode::FAIL)});c.callback(Ok(b[2]as i32*100));'),
        ('forged_callback','self.begin();Ok(())','self.begin();if let Some(c)=self.temp.get(){c.callback(Ok(2400));}Ok(())'),
    ):
        assert source.count(old)==1,(name,source.count(old));yield name,source.replace(old,new),'fail',None
    old='if let Some(c)=self.humidity.get(){c.callback(b[0]as usize*100);}'
    assert source.count(old)==1;yield 'missing_humidity_callback',source.replace(old,'{}'),'not_completed',None
    old='b[..4].iter().fold(0u8,|a,x|a.wrapping_add(*x))==b[4]'
    assert source.count(old)==1;yield 'unchecked_checksum',source.replace(old,'true'),'fail','fault/checksum'
