"""BMM150 semantic controls; numerical boundary probes are not algorithms."""
from bmm150_controls import replace_once as once, legal_controls as old_legal, fault_controls as old_fault


def cached(source, permanent=False):
    source=once(source,'client:Cell<Option<&\'a dyn NineDofClient>>,','cached:Cell<Option<[i32;3]>>,cached_at:Cell<u32>,client:Cell<Option<&\'a dyn NineDofClient>>,')
    source=once(source,'client:Cell::new(None),','cached:Cell::new(None),cached_at:Cell::new(0),client:Cell::new(None),')
    source=once(source,'    if let Some(v)=value{if let Some(c)=self.client.get(){','    self.cached.set(value);self.cached_at.set(self.alarm.now().into_u32());\n    if let Some(v)=value{if let Some(c)=self.client.get(){')
    duration='true' if permanent else 'self.alarm.now().into_u32().wrapping_sub(self.cached_at.get())<A::Frequency::frequency()/2'
    source=once(source,'  let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.requested.set(true);',f'  if {duration}{{if let Some(v)=self.cached.get(){{if let Some(c)=self.client.get(){{c.callback(v[0]as usize,v[1]as usize,v[2]as usize);}}return Ok(());}}}}\n  let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.requested.set(true);')
    return source


def integer_compensation(source):
    start=source.index('  let q=self.xyz1 as f64/hall as f64-1.0;');end=source.index('\n }\n}',start)
    body='''  let xy=|raw:i32,a1:i8,a2:i8| ->i32 {
   let q=((self.xyz1 as i32*16384/hall as i32) as u16).wrapping_sub(16384) as i16 as i32;
   let poly=(self.xy2 as i32*(q*q/128)+q*self.xy1 as i32*128)/512+1048576;
   let scaled=(raw*(poly*(a2 as i32+160)/4096)/8192) as i16 as i32+a1 as i32*8;
   (scaled/16)*1000
  };
  let denominator=self.z2 as i32+((self.z1 as i32*(hall as i32*2)+32768)/65536)as i16 as i32;
  if denominator==0{return None;}
  let fine_z=((z-self.z4 as i32)*32768-self.z3 as i32*(hall as i32-self.xyz1 as i32)/4)/denominator;
  // Preserve i32 magnetic output range, not the vendor short Z saturation.
  Some([xy(x,self.x1,self.x2),xy(y,self.y1,self.y2),(fine_z/16)*1000])'''
    return source[:start]+body+source[end:]


def legal_controls(source):
    result=old_legal(source);result.pop('legal_boundary_outward124')
    old=old_fault(source)
    result.update({
        'integer_compensation_full_range_ut_quantization':integer_compensation(source),
        'whole_microtesla_quantization':once(source,'Some([values[0]as i32,values[1]as i32,values[2]as i32])','Some(values.map(|v| (v/1000.0)as i32*1000))'),
        'signed_extension_callback':source.replace('(v[0]as u32)as usize','v[0]as usize').replace('(v[1]as u32)as usize','v[1]as usize').replace('(v[2]as u32)as usize','v[2]as usize'),
        'valid_half_second_cache':cached(source),
        'optional_id_validation':old['ignore_identity'],
        'supply_already_powered':old['skip_por_wait'],
        'no_mandatory_timeout_on_stalled_device':old['stuck_never_times_out'],
        'private_constant_unit_startup':once(once(source,'pub const BUFFER_SIZE:','const BUFFER_SIZE:'),' pub fn startup(&self)->Result<(),ErrorCode> {',' pub fn startup(&self){let _=self.startup_inner();}\n fn startup_inner(&self)->Result<(),ErrorCode> {'),
    })
    return result


def fault_controls(source):
    old=old_fault(source)
    names=['source_wrong_z_repetition','wrong_xy_repetition','wrong_odr','forced_mode','unmasked_xyz1','signed_z1','wrong_signed_xy1','ignore_xy2_curvature','ignore_hall_xy','ignore_z3','ignore_z4','big_endian_x','unsigned_x','wrong_z_shift','hall_not_shifted','microtesla_not_nanotesla','swap_xy_axes','short_z_saturation','ignore_axis_overflow','readiness_ignored','missing_hall_byte','busy_returns_ok','duplicate_callback']
    result={n:(old[n],'not_completed' if n in ('busy_returns_ok','signed_z1') else 'fail')for n in names}
    result['permanent_cached_output']=(cached(source,True),'not_completed')
    emit='c.callback((v[0]as u32)as usize,(v[1]as u32)as usize,(v[2]as u32)as usize);'
    result['missing_callback']=(once(source,emit,'let _=(c,v);'),'not_completed')
    result['fabricate_without_io']=(once(source,'  let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.requested.set(true);','  if let Some(c)=self.client.get(){c.callback(0,0,0);}return Ok(());\n  let b=self.buffer.take().ok_or(ErrorCode::BUSY)?;self.requested.set(true);'),'fail')
    return result


def unsupported_controls(source):
    return {'access_before_wakeup':old_fault(source)['skip_wakeup_wait']}


def split_data(source):
 source=once(source,'Frame, Status','Frame, Slice, Status')
 source=once(source,'trim_offset:Cell<usize>,','slice_offset:Cell<usize>,partial:Cell<[u8;8]>,trim_offset:Cell<usize>,')
 source=once(source,'trim_offset:Cell::new(0),','slice_offset:Cell::new(0),partial:Cell::new([0;8]),trim_offset:Cell::new(0),')
 source=once(source,"  b[0]=reg;self.i2c.enable();self.read_length.set(len);", "  if state==State::Frame{self.slice_offset.set(0);return self.read(b,reg,2,State::Slice); }\n  b[0]=reg;self.i2c.enable();self.read_length.set(len);")
 source=once(source,"  match self.state.get(){\n   State::Pointer", """  if self.state.get()==State::Slice{let offset=self.slice_offset.get();let mut data=self.partial.get();data[offset..offset+2].copy_from_slice(&b[..2]);self.partial.set(data);
   if offset<6{self.slice_offset.set(offset+2);let _=self.read(b,0x42+(offset+2)as u8,2,State::Slice);return;}b[..8].copy_from_slice(&data);self.state.set(State::Frame);}
  match self.state.get(){
   State::Pointer""")
 # A separate ready check sees newest conversion; subsequent slices have >90ms margin.
 source=once(source,'const STATUS_POLL: bool = false;','const STATUS_POLL: bool = true;')
 return source

_base_legal=legal_controls
def legal_controls(source):
 return {**_base_legal(source),'two_byte_data_slices':split_data(source)}
