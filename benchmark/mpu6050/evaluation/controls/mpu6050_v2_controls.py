"""MPU6050 v2 independent controls; arbitrary100ms deadline no longer a device rule."""
from mpu6050_controls import replace_once as once, legal_controls as old_legal, fault_controls as old_fault


def bounded_cache(s):
    s=old_fault(s)['replay_cached_sample']
    s=once(s,'cached: Cell<bool>,','cache_uses:Cell<u32>,cached: Cell<bool>,')
    s=once(s,'cached:Cell::new(false),','cache_uses:Cell::new(0),cached:Cell::new(false),')
    s=once(s,'if self.cached.get(){','if self.cached.get()&&self.cache_uses.get()<4{self.cache_uses.set(self.cache_uses.get()+1);')
    return once(s,'self.cached.set(true);','self.cache_uses.set(0);self.cached.set(true);')


def legal_controls(source):
    old=old_fault(source);result=old_legal(source)
    result.update({'optional_identity':old['ignore_identity'],'wait_150ms':old['deadline_exceeded'],
        'bounded_four_request_cache':bounded_cache(source),
        'signed_extension_callback':once(source,'xyz[i]=(value as u32) as usize;','xyz[i]=value as usize;'),
        'float32_conversion':once(source,'(raw*9_806_650/16_384) as i32','(raw as f32*9_806_650.0/16_384.0) as i32'),
        'private_constant_unit_startup':once(once(source,'pub const BUFFER_SIZE:','const BUFFER_SIZE:'),'    pub fn startup(&self)->Result<(),ErrorCode> {','    pub fn startup(&self){let _=self.startup_inner();}\n    fn startup_inner(&self)->Result<(),ErrorCode> {'),
    });return result


def fault_controls(source):
    old=old_fault(source);names=['unsigned_counts','little_endian','wrong_range_scaling','milli_g_units','millimeters_units','axis_swap','absolute_axes','omit_wake','wrong_accel_range','wrong_divider','wrong_dlpf','wrong_data_ready_bit','identity_compared_to_other_part','data_offset_is_temperature','missing_z_bytes','duplicate_callback','busy_returns_success','replay_cached_sample','fixed_wait_too_short']
    incomplete={'omit_wake','wrong_data_ready_bit','identity_compared_to_other_part','busy_returns_success','replay_cached_sample'}
    result={n:(old[n],'not_completed'if n in incomplete else'fail')for n in names}
    result['missing_callback']=(once(source,'c.callback(xyz[0],xyz[1],xyz[2]);','let _=(c,xyz);'),'not_completed')
    result['fake_zero_without_io']=(once(source,'        self.polls.set(0);self.status(b)','        self.buffer.replace(b);if let Some(c)=self.client.get(){c.callback(0,0,0);}Ok(())'),'fail')
    return result


def unsupported_controls(source):
    return {'reserved_power_bit':once(source,'(0x6b,0)','(0x6b,0x10)')}
