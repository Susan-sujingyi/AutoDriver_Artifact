"""Evaluator-only BMM150 alternatives and compiling behavior faults."""
def replace_once(s,a,b):
 if s.count(a)!=1:raise AssertionError(f'BMM150 anchor changed {a!r} count{s.count(a)}')
 return s.replace(a,b,1)
def boundary_probe(source,shift):
 # Auxiliary numerical acceptance probes, not additional implementation styles.
 #125 moves outside the real-number bound when the result is fractional;
 #124 stays inside it. Integral results exactly125 away remain acceptable.
 return replace_once(source,'Some([values[0]as i32,values[1]as i32,values[2]as i32])',f'Some(values.map(|v| {{ let n=v as i32; if v>0.0 {{n-{shift}}} else {{n+{shift}}} }}))')
def legal_controls(source):
 changes={
  'poll_2ms':('const POLL_US: u32 = 5000;','const POLL_US: u32 = 2000;'),
  'poll_10ms':('const POLL_US: u32 = 5000;','const POLL_US: u32 = 10000;'),
  'trim_chunks7':('const TRIM_CHUNK: usize = 21;','const TRIM_CHUNK: usize = 7;'),
  'trim_single_bytes':('const TRIM_CHUNK: usize = 21;','const TRIM_CHUNK: usize = 1;'),
  'stop_separated_pointer':('const SPLIT_READ: bool = false;','const SPLIT_READ: bool = true;'),
  'soft_reset':('const SOFT_RESET: bool = false;','const SOFT_RESET: bool = true;'),
  'status_then_frame':('const STATUS_POLL: bool = false;','const STATUS_POLL: bool = true;'),
  'frame_with_interrupt_status':('const FRAME_LEN: usize = 8;','const FRAME_LEN: usize = 9;'),
  'reverse_config_order':('&[(0x51,0x04),(0x52,0x0e),(0x4c,0x00)]','&[(0x4c,0x06),(0x52,0x0e),(0x51,0x04),(0x4c,0x00)]'),
  'unused_interrupt_pins_enabled':('&[(0x51,0x04),(0x52,0x0e),(0x4c,0x00)]','&[(0x51,0x04),(0x52,0x0e),(0x4e,0xc7),(0x4c,0x00)]'),
  'quantized_16lsb_per_ut':('Some([values[0]as i32,values[1]as i32,values[2]as i32])','Some(values.map(|v| ((v / 62.5) as i64 * 125 / 2) as i32))'),
 }
 results={'legal_'+n:replace_once(source,a,b)for n,(a,b)in changes.items()}
 results['legal_poll_2ms']=replace_once(results['legal_poll_2ms'],'const MAX_POLLS: u8 = 40;','const MAX_POLLS: u8 = 110;')
 results['legal_poll_10ms']=replace_once(results['legal_poll_10ms'],'const MAX_POLLS: u8 = 40;','const MAX_POLLS: u8 = 22;')
 results['legal_f32_compensation']=source.replace('as f64','as f32')
 results['legal_boundary_outward124']=boundary_probe(source,124)
 return results

def fault_controls(source):
 changes={
  'source_wrong_z_repetition':('(0x52,0x0e)','(0x52,0x07)'),
  'wrong_xy_repetition':('(0x51,0x04)','(0x51,0x09)'),
  'wrong_odr':('(0x4c,0x00)','(0x4c,0x08)'),
  'forced_mode':('(0x4c,0x00)','(0x4c,0x02)'),
  'skip_wakeup_wait':('const POWER_US: u32 = 3000;','const POWER_US: u32 = 0;'),
  'skip_por_wait':('const POR_US: u32 = 1000;','const POR_US: u32 = 0;'),
  'ignore_identity':('if b[0]!=0x32','if false'),
  'unmasked_xyz1':('b[16]&0x7f','b[16]'),
  'signed_z1':('z1:u16::from_le_bytes([b[13],b[14]])','z1:(i16::from_le_bytes([b[13],b[14]]).max(0))as u16'),
  'wrong_signed_xy1':('self.xy1 as f64*q','self.xy1 as i8 as f64*q'),
  'ignore_xy2_curvature':('self.xy2 as f64*q*q','0.0*q*q'),
  'ignore_hall_xy':('let q=self.xyz1 as f64/hall as f64-1.0;','let q=0.0;'),
  'ignore_z3':('self.z3 as f64*(hall as f64-self.xyz1 as f64)','0.0*(hall as f64-self.xyz1 as f64)'),
  'ignore_z4':('z as f64-self.z4 as f64','z as f64'),
  'big_endian_x':('i16::from_le_bytes([b[0],b[1]])>>3','i16::from_be_bytes([b[0],b[1]])>>3'),
  'unsigned_x':('i16::from_le_bytes([b[0],b[1]])>>3','u16::from_le_bytes([b[0],b[1]])>>3'),
  'wrong_z_shift':('i16::from_le_bytes([b[4],b[5]])>>1','i16::from_le_bytes([b[4],b[5]])>>3'),
  'hall_not_shifted':('u16::from_le_bytes([b[6],b[7]])>>2','u16::from_le_bytes([b[6],b[7]])'),
  'microtesla_not_nanotesla':('[cx*1000.0,cy*1000.0,cz*1000.0]','[cx,cy,cz]'),
  'swap_xy_axes':('Some([values[0]as i32,values[1]as i32,values[2]as i32])','Some([values[1]as i32,values[0]as i32,values[2]as i32])'),
  'short_z_saturation':('cz*1000.0','cz.max(-2047.9375).min(2047.9375)*1000.0'),
  'ignore_axis_overflow':('x == -4096 || y == -4096 || z == -16384 || hall == 0','hall == 0'),
  'fabricate_zero_denominator':('if denominator == 0.0 { return None; }','if denominator == 0.0 { return Some([0;3]); }'),
  'readiness_ignored':('if !STATUS_POLL && b[6]&1==0','if false'),
  'stuck_never_times_out':('const MAX_POLLS: u8 = 40;','const MAX_POLLS: u8 = 250;'),
  'deadline_too_short_for_odr':('const MAX_POLLS: u8 = 40;','const MAX_POLLS: u8 = 10;'),
  'missing_hall_byte':('const FRAME_LEN: usize = 8;','const FRAME_LEN: usize = 7;'),
  'error_fabricates_zero':('fn fail(&self){','fn fail(&self){if self.requested.get(){if let Some(c)=self.client.get(){c.callback(0,0,0);}}'),
  'lose_async_buffer':('if status.is_err(){self.buffer.replace(b);self.fail();return;}','if status.is_err(){let _=b;self.fail();return;}'),
  'busy_returns_ok':('State::Off=>return Err(ErrorCode::OFF),_=>return Err(ErrorCode::BUSY)','State::Off=>return Err(ErrorCode::OFF),_=>return Ok(())'),
 }
 results={n:replace_once(source,a,b)for n,(a,b)in changes.items()}
 emit='c.callback((v[0]as u32)as usize,(v[1]as u32)as usize,(v[2]as u32)as usize);'
 results['duplicate_callback']=replace_once(source,emit,emit+emit)
 fallback=replace_once(source,'let hall=(u16::from_le_bytes([b[6],b[7]])>>2)as u32;','let mut hall=(u16::from_le_bytes([b[6],b[7]])>>2)as u32; if hall==0 {hall=self.xyz1 as u32;}')
 results['hall_zero_fallback_for_xyz']=fallback
 results['fault_boundary_outward125']=boundary_probe(source,125)
 return results
