"""Evaluator-only LSM6DSO implementation controls, not method examples."""
def once(s,a,b):
    if s.count(a)!=1:raise AssertionError('LSM6DSO anchor changed: '+repr(a))
    return s.replace(a,b,1)

def discard_first(s):
    s=once(s,'(0x13,0x08)','(0x13,0)')
    s=once(s,'config:Cell<usize>,','settled:Cell<bool>,config:Cell<usize>,')
    s=once(s,'config:Cell::new(0),','settled:Cell::new(false),config:Cell::new(0),')
    s=once(s,'self.config.set(0);self.polls.set(0);','self.settled.set(false);self.config.set(0);self.polls.set(0);')
    return once(s,'State::Status=>{if b[0]&1!=0{','''State::Status=>{if b[0]&1!=0 && !self.settled.get(){self.settled.set(true);b[0]=0x28;self.state.set(State::Discard);let _=self.submit(b,1,6);return}if b[0]&1!=0{''')

def split_frame(s,step=2,high_first=False):
    s=once(s,'config:Cell<usize>,','offset:Cell<usize>,partial:Cell<[u8;6]>,config:Cell<usize>,')
    s=once(s,'config:Cell::new(0),','offset:Cell::new(0),partial:Cell::new([0;6]),config:Cell::new(0),')
    start=0x29 if high_first else 0x28
    s=once(s,'b[0]=0x28;self.state.set(State::Discard);self.submit(b,1,6)',f'self.offset.set(0);b[0]={start};self.state.set(State::Discard);self.submit(b,1,{step})')
    s=once(s,'b[0]=0x28;self.state.set(State::Data);let _=self.submit(b,1,6);',f'self.offset.set(0);b[0]={start};self.state.set(State::Data);let _=self.submit(b,1,{step});')
    mapping='(offset ^ 1)' if high_first else 'offset'
    helper='''    fn collect_frame(&self,b:&'static mut[u8])->Option<&'static mut[u8]>{
        let offset=self.offset.get();let mut frame=self.partial.get();
        frame[MAPPING..MAPPING+STEP].copy_from_slice(&b[..STEP]);self.partial.set(frame);
        if offset+STEP<6{let next=offset+STEP;self.offset.set(next);b[0]=0x28+NEXT as u8;let _=self.submit(b,1,STEP);None}
        else{b[..6].copy_from_slice(&frame);Some(b)}
    }
'''.replace('MAPPING',mapping).replace('STEP',str(step)).replace('NEXT','(next ^ 1)' if high_first else 'next')
    s=once(s,'    fn request(&self)',helper+'    fn request(&self)')
    s=once(s,'State::Discard=>{let _=self.status(b);}', 'State::Discard=>{if let Some(b)=self.collect_frame(b){let _=self.status(b);}}')
    s=once(s,'State::Data=>{let mut xyz=', 'State::Data=>{let b=match self.collect_frame(b){Some(b)=>b,None=>return};let mut xyz=')
    if step==1:s=once(s,'(0x12,0x44)','(0x12,0x40)')
    return s

def pointer_stop(s):
    s=once(s,'Off,Bank','Off,Pointer,Bank')
    s=once(s,'config:Cell<usize>,','read_state:Cell<State>,read_len:Cell<usize>,config:Cell<usize>,')
    s=once(s,'config:Cell::new(0),','read_state:Cell::new(State::Off),read_len:Cell::new(0),config:Cell::new(0),')
    s=once(s,'else{self.i2c.write_read(b,w,r)}','else{self.read_state.set(self.state.get());self.read_len.set(r);self.state.set(State::Pointer);self.i2c.write(b,w)}')
    s=once(s,'Err((e,b))=>{self.buffer.replace(b);self.terminal();Err(e.into())}','Err((e,b))=>{if self.state.get()==State::Pointer{self.state.set(self.read_state.get());}self.buffer.replace(b);self.terminal();Err(e.into())}')
    return once(s,'        if status.is_err(){self.buffer.replace(b);self.terminal();return}','''        if self.state.get()==State::Pointer{self.state.set(self.read_state.get());if status.is_err(){self.buffer.replace(b);self.terminal();return}if let Err((_e,b))=self.i2c.read(b,self.read_len.get()){self.buffer.replace(b);self.terminal();}return}
        if status.is_err(){self.buffer.replace(b);self.terminal();return}''')

def legal_controls(s):
    full=once(s,'b[0]=0x28;self.state.set(State::Data);let _=self.submit(b,1,6);','b[0]=0x20;self.state.set(State::Data);let _=self.submit(b,1,14);')
    full=once(full,'[b[2*i],b[2*i+1]]','[b[8+2*i],b[9+2*i]]')
    fixed=once(s,'State::Reset=>{b[0]=0x12;self.state.set(State::ResetCheck);let _=self.submit(b,1,1);}','State::Reset=>{self.buffer.replace(b);self.wait(50,State::ResetWait);}')
    fixed=once(fixed,'State::ResetWait=>{b[0]=0x12;self.state.set(State::ResetCheck);let _=self.submit(b,1,1);}','State::ResetWait=>{self.configure(b);}')
    return {
        'legal_equivalent_2g_fullscale_encoding':once(s,'(0x17,0),(0x10,0x40)','(0x17,2),(0x10,0x44)'),
        'legal_explicit_discard_first_without_drdy_mask':discard_first(s),
        'legal_pair_reads':split_frame(s),
        'legal_single_byte_no_auto_increment':split_frame(s,1),
        'legal_high_byte_first_pairs':split_frame(s,1,True),
        'legal_pointer_stop_reads':pointer_stop(s),
        'legal_14byte_including_gyro_temperature':full,
        'legal_fixed_reset_wait':fixed,
        'legal_no_reset_default_configuration':once(s,'b[0]=0x12;b[1]=0x05;self.state.set(State::Reset);let _=self.submit(b,2,0);','self.configure(b);'),
        'legal_inactive_irq_spi_bits':once(s,'(0x12,0x44)','(0x12,0x7c)'),
        'legal_configuration_reorder':once(s,'(0x12,0x44),(0x13,0x08),(0x18,0xe2),(0x11,0)','(0x18,0xe2),(0x11,0),(0x13,0x08),(0x12,0x44)'),
        'legal_poll_5ms':once(once(s,'const POLL_US:u32=1_000;','const POLL_US:u32=5_000;'),'const MAX_POLLS:u32=80;','const MAX_POLLS:u32=16;'),
        'legal_nearest_integer':once(s,'raw*61*9_806_650/1_000_000','(raw*61*9_806_650+if raw>=0{500_000}else{-500_000})/1_000_000'),
    }

def fault_controls(s):
    changes={
        'unsigned_samples':('i16::from_le_bytes','u16::from_le_bytes'),
        'big_endian':('i16::from_le_bytes','i16::from_be_bytes'),
        'mpu6050_sensitivity':('raw*61*9_806_650/1_000_000','raw*9_806_650/16_384'),
        'wrong_range_scaling':('raw*61*9_806_650/1_000_000','raw*122*9_806_650/1_000_000'),
        'mg_units':('raw*61*9_806_650/1_000_000','raw*61/1000'),
        'raw_is_acceleration':('raw*61*9_806_650/1_000_000','raw'),
        'swap_axes':('c.callback(xyz[0],xyz[1],xyz[2]);','c.callback(xyz[1],xyz[0],xyz[2]);'),
        'lose_sign':('xyz[i]=(value as u32) as usize;','xyz[i]=(value.abs() as u32) as usize;'),
        'unsettled_first_sample':('(0x13,0x08)','(0x13,0)'),
        'wrong_odr':('(0x10,0x40)','(0x10,0x30)'),
        'wrong_acceleration_range':('(0x10,0x40)','(0x10,0x48)'),
        'enable_gyro':('(0x11,0)','(0x11,0x40)'),
        'disable_bdu':('(0x12,0x44)','(0x12,0x04)'),
        'burst_without_auto_increment':('(0x12,0x44)','(0x12,0x40)'),
        'low_normal_power':('(0x15,0)','(0x15,0x10)'),
        'wrong_ready_bit':('State::Status=>{if b[0]&1!=0','State::Status=>{if b[0]&2!=0'),
        'wrong_bank':('b[0]=0x01;b[1]=0;','b[0]=0x01;b[1]=0x80;'),
        'ignore_identity':('if b[0]!=0x6c','if false'),
        'wrong_id':('if b[0]!=0x6c','if b[0]!=0x69'),
        'duplicate_callback':('c.callback(xyz[0],xyz[1],xyz[2]);','c.callback(xyz[0],xyz[1],xyz[2]);c.callback(xyz[0],xyz[1],xyz[2]);'),
        'error_buffer_lost':('self.buffer.replace(b);self.terminal();Err(e.into())','let _=b;self.terminal();Err(e.into())'),
        'fabricate_scalar_error':('if status.is_err(){self.buffer.replace(b);self.terminal();return}','if status.is_err(){self.buffer.replace(b);self.terminal();if let Some(c)=self.client.get(){c.callback(0,0,0);}return}'),
        'busy_success':('State::Off=>return Err(ErrorCode::OFF),_=>return Err(ErrorCode::BUSY)','State::Off=>return Err(ErrorCode::OFF),_=>return Ok(())'),
        'deadline_exceeded':('const POLL_US:u32=1_000;','const POLL_US:u32=150_000;'),
    }
    result={name:once(s,a,b) for name,(a,b) in changes.items()}
    # Two correctly latched byte pairs separated by ten milliseconds still do not
    # provide one XYZ frame: pair BDU is insufficient for inter-axis coherence.
    torn=split_frame(s)
    torn=once(torn,'Off,Bank','Off,SliceWait,Bank')
    torn=once(torn,'offset:Cell<usize>,','slice_state:Cell<State>,offset:Cell<usize>,')
    torn=once(torn,'offset:Cell::new(0),','slice_state:Cell::new(State::Off),offset:Cell::new(0),')
    torn=once(torn,'let _=self.submit(b,1,2);None','self.slice_state.set(self.state.get());self.buffer.replace(b);self.wait(10_000,State::SliceWait);None')
    torn=once(torn,'match self.state.get(){State::Waiting=>','match self.state.get(){State::SliceWait=>{self.state.set(self.slice_state.get());let _=self.submit(b,1,2);},State::Waiting=>')
    result['bdu_pair_is_not_xyz_frame']=torn
    cached=once(s,'config:Cell<usize>,','cached:Cell<Option<[usize;3]>>,config:Cell<usize>,')
    cached=once(cached,'config:Cell::new(0),','cached:Cell::new(None),config:Cell::new(0),')
    cached=once(cached,'let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.polls.set(0);','''let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.polls.set(0);
        if let Some(xyz)=self.cached.get(){self.buffer.replace(b);if let Some(c)=self.client.get(){c.callback(xyz[0],xyz[1],xyz[2]);}return Ok(())}''')
    cached=once(cached,'xyz[i]=(value as u32) as usize;}self.buffer.replace(b);','xyz[i]=(value as u32) as usize;}self.cached.set(Some(xyz));self.buffer.replace(b);')
    result['replay_cached_sample']=cached
    return result
