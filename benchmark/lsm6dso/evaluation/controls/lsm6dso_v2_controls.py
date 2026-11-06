"""LSM6DSO v2 controls preserve pair BDU's actual, limited guarantee."""
from lsm6dso_controls import once, legal_controls as old_legal, fault_controls as old_fault


def fixed_wait(source):
    s=once(source,'(0x13,0x08)','(0x13,0)')
    s=once(s,'b[0]=0x28;self.state.set(State::Discard);self.submit(b,1,6)','self.buffer.replace(b);self.wait(40_000,State::Waiting);Ok(())')
    s=once(s,'State::Waiting=>{let _=self.status(b);}','State::Waiting=>{b[0]=0x28;self.state.set(State::Data);let _=self.submit(b,1,6);}')
    return s


def bounded_cache(s):
    s=old_fault(s)['replay_cached_sample']
    s=once(s,'cached:Cell<Option<[usize;3]>>,','cache_uses:Cell<u32>,cached:Cell<Option<[usize;3]>>,')
    s=once(s,'cached:Cell::new(None),','cache_uses:Cell::new(0),cached:Cell::new(None),')
    s=once(s,'if let Some(xyz)=self.cached.get(){','if let Some(xyz)=self.cached.get().filter(|_|self.cache_uses.get()<4){self.cache_uses.set(self.cache_uses.get()+1);')
    return once(s,'self.cached.set(Some(xyz));','self.cache_uses.set(0);self.cached.set(Some(xyz));')


def legal_controls(source):
    old=old_fault(source);result=old_legal(source)
    result.update({'different_epochs_across_axes':old['bdu_pair_is_not_xyz_frame'],
        'synchronized_reads_without_bdu':old['disable_bdu'],
        'optional_identity':old['ignore_identity'],
        'bounded_four_request_cache':bounded_cache(source),
        'sufficient_wait_without_ready':fixed_wait(source),
        'signed_extension_callback':once(source,'xyz[i]=(value as u32) as usize;','xyz[i]=value as usize;'),
        'float32_conversion':once(source,'(raw*61*9_806_650/1_000_000) as i32','(raw as f32*61.0*9_806_650.0/1_000_000.0) as i32'),
        'private_constant_unit_startup':once(once(source,'pub const BUFFER_SIZE:','const BUFFER_SIZE:'),'    pub fn startup(&self)->Result<(),ErrorCode>{','    pub fn startup(&self){let _=self.startup_inner();}\n    fn startup_inner(&self)->Result<(),ErrorCode>{'),
    });return result


def fault_controls(source):
    old=old_fault(source);names=['unsigned_samples','big_endian','mpu6050_sensitivity','wrong_range_scaling','mg_units','raw_is_acceleration','swap_axes','lose_sign','unsettled_first_sample','wrong_odr','wrong_acceleration_range','enable_gyro','burst_without_auto_increment','low_normal_power','wrong_ready_bit','wrong_bank','wrong_id','duplicate_callback','busy_success','replay_cached_sample']
    incomplete={'wrong_ready_bit','wrong_bank','wrong_id','busy_success','replay_cached_sample'}
    result={n:(old[n],'not_completed'if n in incomplete else'fail')for n in names}
    result['missing_callback']=(once(source,'c.callback(xyz[0],xyz[1],xyz[2]);','let _=(c,xyz);'),'not_completed')
    result['fake_zero_without_io']=(once(source,'b[0]=0x28;self.state.set(State::Discard);self.submit(b,1,6)','self.buffer.replace(b);if let Some(c)=self.client.get(){c.callback(0,0,0);}Ok(())'),'fail')
    return result


def unsupported_controls(source):
    return {'reserved_bank_bits':once(source,'b[0]=0x01;b[1]=0;','b[0]=0x01;b[1]=1;')}
