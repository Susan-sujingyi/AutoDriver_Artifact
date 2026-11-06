"""BH1750FVI v2 host controls; no method development use."""
from bh1750_controls import legal_controls as old_legal, fault_controls as old_fault, replace_once


def bounded_cache(source):
    return old_fault(source)['replay_cached_sample'].replace('if let Some(value) = self.cached.get() {','if let Some(value) = self.cached.take() {')


def startup_prefetch(source):
    source=bounded_cache(source)
    source=replace_once(source,'    cached: Cell<Option<usize>>,','    cached: Cell<Option<usize>>,\n    prefetching: Cell<bool>,')
    source=replace_once(source,'cached: Cell::new(None),','cached: Cell::new(None), prefetching: Cell::new(false),')
    source=replace_once(source,'        self.state.set(State::Ready);\n        Ok(())','        self.state.set(State::Ready);\n        self.prefetching.set(true);\n        self.request()')
    source=replace_once(source,'                if let Some(client) = self.client.get() { client.callback(lux); }','                if !self.prefetching.replace(false) {if let Some(client) = self.client.get() { client.callback(lux); }}')
    return source


def legal_controls(source):
    result=old_legal(source)
    result.update({
        'wait_300ms':source.replace('const WAIT_US: u32 = 180_000;','const WAIT_US: u32 = 300_000;'),
        'bounded_reuse_completed_reading':bounded_cache(source),
        'prefetch_during_initialization':startup_prefetch(source),
        'private_constant_unit_startup':source.replace('pub const BUFFER_SIZE:','const BUFFER_SIZE:').replace('    pub fn startup(&self) -> Result<(), ErrorCode> {','    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self) -> Result<(), ErrorCode> {'),
    })
    return result


def fault_controls(source):
    old=old_fault(source)
    keys=('lux_zero_to_usize_max','little_endian','signed_sample','raw_is_lux','high_resolution2_scaling',
          'millilux_instead_of_lux','typical_wait_only','no_wait','continuous_mode','wrong_resolution_mode',
          'omit_measurement_command','wrong_mtreg','reset_while_powered_down','register_style_write',
          'one_byte_read','source_style_retrigger_read','duplicate_callback')
    result={k:(old[k],'fail') for k in keys}
    result.update({k:(old[k],'not_completed') for k in ('busy_returns_ok','replay_cached_sample')})
    result['missing_callback']=(source.replace('client.callback(lux);','let _=(client,lux);'),'not_completed')
    return result
