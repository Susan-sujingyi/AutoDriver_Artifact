"""Evaluator-only behavioral diversity and compiling counterexamples."""
def replace_once(s,a,b):
    if s.count(a)!=1:raise AssertionError('MPU6050 anchor changed: '+repr(a))
    return s.replace(a,b,1)

def fixed_wait(s):
    # No DATA_RDY enable/polling; healthy digital model guarantees first frame by20ms.
    s=replace_once(s,'(0x38,1)','(0x38,0)')
    s=replace_once(s,'self.polls.set(0);self.status(b)', '''self.polls.set(0);self.buffer.replace(b);self.state.set(State::Waiting);
        let ticks=(A::Frequency::frequency() as u64 * 25_000 +999_999)/1_000_000;
        self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));Ok(())''')
    s=replace_once(s,'let _=self.status(b);','b[0]=0x3b;self.state.set(State::Data);let _=self.submit(b,1,6);')
    s=replace_once(s,'self.result.set(xyz);b[0]=0x3a;self.state.set(State::Flush);let _=self.submit(b,1,1);','self.result.set(xyz);self.buffer.replace(b);self.terminal();if let Some(c)=self.client.get(){c.callback(xyz[0],xyz[1],xyz[2]);}')
    return s

def split_reads(s):
    s=replace_once(s,'result: Cell<[usize;3]>,','result: Cell<[usize;3]>, offset: Cell<usize>, partial: Cell<[u8;6]>,')
    s=replace_once(s,'result:Cell::new([0;3]),','result:Cell::new([0;3]),offset:Cell::new(0),partial:Cell::new([0;6]),')
    s=replace_once(s,'b[0]=0x3b;self.state.set(State::Data);let _=self.submit(b,1,6);','self.offset.set(0);b[0]=0x3b;self.state.set(State::Data);let _=self.submit(b,1,2);')
    s=replace_once(s,'            State::Data=>{','''            State::Data=>{
                let offset=self.offset.get();let mut frame=self.partial.get();frame[offset..offset+2].copy_from_slice(&b[..2]);self.partial.set(frame);
                if offset<4 {self.offset.set(offset+2);b[0]=0x3b+(offset+2) as u8;let _=self.submit(b,1,2);return}
                b[..6].copy_from_slice(&frame);''')
    s=replace_once(s,'            State::Flush=>{','''            State::Flush=>{
                if b[0]&1!=0 {let _=self.status(b);return}''')
    # Polling status can be nearly one period old; align reads to a new edge using
    # a full period wait after each status check to avoid cross-epoch slices.
    # Instead use status interval100us: three200us-bounded readings remain within1250us.
    return replace_once(s,'const POLL_US: u32 = 1_000;','const POLL_US: u32 = 100;').replace('const MAX_POLLS: u32 = 50;','const MAX_POLLS: u32 = 400;')

def soft_reset(s):
    s=replace_once(s,'Off, Identity','Off, Reset, ResetWait, Identity')
    s=replace_once(s,'State::Identity|State::Configure','State::Identity|State::Configure|State::Reset|State::ResetWait')
    s=replace_once(s,'b[0]=0x75;self.state.set(State::Identity);self.config.set(0);self.submit(b,1,1)',
                   'b[0]=0x6b;b[1]=0x80;self.state.set(State::Reset);self.config.set(0);self.submit(b,2,0)')
    s=replace_once(s,'        match self.state.get() {\n            State::Identity','''        match self.state.get() {
            State::Reset=>{self.buffer.replace(b);self.state.set(State::ResetWait);let ticks=(A::Frequency::frequency() as u64*3000+999_999)/1_000_000;self.alarm.set_alarm(self.alarm.now(),A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));},
            State::Identity''')
    return replace_once(s,'    fn alarm(&self) {if self.state.get()==State::Waiting','''    fn alarm(&self) {if self.state.get()==State::ResetWait {if let Some(b)=self.buffer.take(){b[0]=0x75;self.state.set(State::Identity);let _=self.submit(b,1,1);}return}if self.state.get()==State::Waiting''')

def pointer_stop_reads(s):
    s=replace_once(s,'Off, Identity','Off, Pointer, Identity')
    s=replace_once(s,'config: Cell<usize>,','read_state: Cell<State>, read_len: Cell<usize>, config: Cell<usize>,')
    s=replace_once(s,'config:Cell::new(0),','read_state:Cell::new(State::Off),read_len:Cell::new(0),config:Cell::new(0),')
    s=replace_once(s,'else {self.i2c.write_read(b,w,r)}',
        'else {self.read_state.set(self.state.get());self.read_len.set(r);self.state.set(State::Pointer);self.i2c.write(b,w)}')
    s=replace_once(s,'Err((e,b))=>{self.buffer.replace(b);self.terminal();Err(e.into())}',
        'Err((e,b))=>{if self.state.get()==State::Pointer{self.state.set(self.read_state.get());}self.buffer.replace(b);self.terminal();Err(e.into())}')
    return replace_once(s,'        if status.is_err() {self.buffer.replace(b);self.terminal();return}','''        if self.state.get()==State::Pointer {
            self.state.set(self.read_state.get());
            if status.is_err(){self.buffer.replace(b);self.terminal();return}
            if let Err((_e,b))=self.i2c.read(b,self.read_len.get()){self.buffer.replace(b);self.terminal();}return
        }
        if status.is_err() {self.buffer.replace(b);self.terminal();return}''')

def legal_controls(s):
    return {
        'legal_pointer_stop_reads':pointer_stop_reads(s),
        'legal_clear_status_on_any_read':replace_once(s,'(0x38,1)','(0x37,0x10), (0x38,1)'),
        'legal_software_reset':soft_reset(s),
        'legal_full_14byte_frame':replace_once(s,'let _=self.submit(b,1,6);','let _=self.submit(b,1,14);'),
        'legal_floor_negative_rounding':replace_once(s,'raw*9_806_650/16_384','(raw*9_806_650)>>14'),
        'legal_rounded_signed_values':replace_once(s,'raw*9_806_650/16_384','(raw*9_806_650+if raw>=0{8192}else{-8192})/16_384'),
        'legal_alternate_setup_order':replace_once(s,'&[(0x6b,0), (0x1c,0), (0x1b,0), (0x19,9), (0x38,1)]','&[(0x1c,0), (0x19,9), (0x1b,0), (0x38,1), (0x6b,0)]'),
        'legal_explicit_dlpf_pll':replace_once(s,'(0x6b,0), (0x1c,0)','(0x1a,0), (0x6b,1), (0x1c,0)'),
        'legal_fixed_wait_no_ready_poll':fixed_wait(s),
        'legal_split_axis_reads':split_reads(s),
    }

def fault_controls(s):
    changes={
        'unsigned_counts':('i16::from_be_bytes([b[2*i],b[2*i+1]])','u16::from_be_bytes([b[2*i],b[2*i+1]])'),
        'little_endian':('i16::from_be_bytes([b[2*i],b[2*i+1]])','i16::from_le_bytes([b[2*i],b[2*i+1]])'),
        'wrong_range_scaling':('raw*9_806_650/16_384','raw*9_806_650/8_192'),
        'milli_g_units':('raw*9_806_650/16_384','raw*1000/16_384'),
        'millimeters_units':('raw*9_806_650/16_384','raw*9806/16_384'),
        'axis_swap':('c.callback(xyz[0],xyz[1],xyz[2]);','c.callback(xyz[1],xyz[0],xyz[2]);'),
        'absolute_axes':('xyz[i]=(value as u32) as usize;','xyz[i]=(value.abs() as u32) as usize;'),
        'omit_wake':('(0x6b,0)','(0x6b,0x40)'),
        'wrong_accel_range':('(0x1c,0)','(0x1c,8)'),
        'wrong_divider':('(0x19,9)','(0x19,7)'),
        'wrong_dlpf':('(0x19,9), (0x38,1)','(0x19,9), (0x1a,1), (0x38,1)'),
        'wrong_data_ready_bit':('if b[0]&1!=0','if b[0]&2!=0'),
        'ignore_identity':('if b[0]!=0x68','if false'),
        'identity_compared_to_other_part':('if b[0]!=0x68','if b[0]!=0x71'),
        'data_offset_is_temperature':('b[0]=0x3b;self.state.set(State::Data);','b[0]=0x41;self.state.set(State::Data);'),
        'missing_z_bytes':('let _=self.submit(b,1,6);','let _=self.submit(b,1,4);'),
        'duplicate_callback':('c.callback(xyz[0],xyz[1],xyz[2]);','c.callback(xyz[0],xyz[1],xyz[2]);c.callback(xyz[0],xyz[1],xyz[2]);'),
        'lose_error_buffer':('self.buffer.replace(b);self.terminal();Err(e.into())','let _=b;self.terminal();Err(e.into())'),
        'busy_returns_success':('State::Off=>return Err(ErrorCode::OFF),_=>return Err(ErrorCode::BUSY)','State::Off=>return Err(ErrorCode::OFF),_=>return Ok(())'),
        'fabricate_error_zero':('if status.is_err() {self.buffer.replace(b);self.terminal();return}','if status.is_err() {self.buffer.replace(b);self.terminal();if let Some(c)=self.client.get(){c.callback(0,0,0);}return}'),
    }
    result={name:replace_once(s,a,b) for name,(a,b) in changes.items()}
    result['fixed_wait_too_short']=replace_once(fixed_wait(s),'* 25_000','* 1_000')
    result['deadline_exceeded']=replace_once(fixed_wait(s),'* 25_000','* 150_000')
    cached=replace_once(s,'result: Cell<[usize;3]>,','result: Cell<[usize;3]>, cached: Cell<bool>,')
    cached=replace_once(cached,'result:Cell::new([0;3]),','result:Cell::new([0;3]),cached:Cell::new(false),')
    cached=replace_once(cached,'        self.polls.set(0);self.status(b)','''        if self.cached.get(){let xyz=self.result.get();self.buffer.replace(b);if let Some(c)=self.client.get(){c.callback(xyz[0],xyz[1],xyz[2]);}return Ok(())}
        self.polls.set(0);self.status(b)''')
    cached=replace_once(cached,'let xyz=self.result.get();self.buffer.replace(b);self.terminal();','let xyz=self.result.get();self.cached.set(true);self.buffer.replace(b);self.terminal();')
    result['replay_cached_sample']=cached
    return result
