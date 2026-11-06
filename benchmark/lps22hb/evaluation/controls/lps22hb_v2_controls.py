"""Host-only semantic controls; never method outputs or input materials."""
from lps22hb_controls import legal_controls as old_legal, fault_controls as old_fault, replace_once


def cached_other_channel(source):
    source=replace_once(source,'#[derive(Clone, Copy)]\nenum Channel','#[derive(Clone, Copy, PartialEq)]\nenum Channel')
    source=replace_once(source,'    sample: Cell<[u8; 5]>,','    sample: Cell<[u8; 5]>,\n    cached: Cell<Option<Channel>>,')
    source=replace_once(source,'            sample: Cell::new([0; 5]),','            sample: Cell::new([0; 5]),\n            cached: Cell::new(None),')
    source=replace_once(source,'        let channel = self.pending.take();\n        self.buffer.replace(buffer);','''        let channel = self.pending.take();
        self.cached.set(channel.map(|c|if c==Channel::Temperature{Channel::Pressure}else{Channel::Temperature}));
        self.buffer.replace(buffer);''')
    source=replace_once(source,'        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;\n        self.pending.set(Some(channel));','''        if self.cached.get()==Some(channel){
            self.cached.set(None);let bytes=self.sample.get();
            match channel{
                Channel::Temperature=>if let Some(client)=self.temperature_client.get(){client.callback(Ok(i16::from_le_bytes([bytes[3],bytes[4]]) as i32));},
                Channel::Pressure=>if let Some(client)=self.pressure_client.get(){client.callback(Ok(u32::from_le_bytes([bytes[0],bytes[1],bytes[2],0])/4096));},
            }return Ok(());
        }
        self.cached.set(None);
        let buffer = self.buffer.take().ok_or(ErrorCode::BUSY)?;
        self.pending.set(Some(channel));''')
    return source


def fixed_wait(source):
    source=source.replace('const FIRST_POLL_MS: u32 = 20;','const FIRST_POLL_MS: u32 = 1200;')
    source=replace_once(source,'        let result = self.read_register(buffer, 0x27, State::Status);','        self.wait(buffer);\n        let result = Ok(());')
    source=replace_once(source,'            if let Err(error) = self.read_register(buffer, 0x27, State::Status) {','            self.output_index.set(0);\n            if let Err(error) = self.read_register(buffer, OUTPUT_ORDER[0].0, State::Output) {')
    return source


def legal_controls(source):
    result=old_legal(source)
    result.update({
        'cached_other_channel':cached_other_channel(source),
        'fixed_wait_without_ready_poll':fixed_wait(source),
        'no_identity_probe':replace_once(source,'let result = self.read_register(buffer, 0x0f, State::Identity);','let result = self.write_register(buffer,0x11,0,State::Interface);'),
        'pressure_round_nearest':source.replace('let pressure = raw_pressure / 4096;','let pressure = (raw_pressure + 2048) / 4096;'),
        'filter_disabled_unused_bandwidth':source.replace('0x10, 0x12, State::Configure','0x10, 0x16, State::Configure'),
        'private_constant_unit_startup':source.replace('pub const BUFFER_SIZE:','const BUFFER_SIZE:').replace('    pub fn startup(&self) -> Result<(), ErrorCode> {','    pub fn startup(&self) {let _=self.startup_inner();}\n    fn startup_inner(&self) -> Result<(), ErrorCode> {'),
        'different_busy_error':source.replace('else { ErrorCode::BUSY }','else { ErrorCode::FAIL }'),
    })
    return result


def fault_controls(source):
    previous=old_fault(source)
    result={k:(previous[k],'fail') for k in ('wrong_pressure_unit_pa','unsigned_temperature','wrong_temperature_unit_celsius',
        'duplicate_temperature_callback','ignore_readiness','wrong_odr_10hz','disable_bdu','enable_auto_increment_with_bdu','pressure_high_not_last')}
    result['missing_temperature']=(source.replace('client.callback(Ok(temperature));','let _=(client,temperature);'),'not_completed')
    result['fabricated_temperature']=(source.replace('client.callback(Ok(temperature));','client.callback(Ok(0));'),'fail')
    result['wrong_pressure_byte_order']=(source.replace('[bytes[0], bytes[1], bytes[2], 0]','[bytes[2], bytes[1], bytes[0], 0]'),'fail')
    # Valid cache when consumed once; this mutation never consumes the selected
    # cached channel, exposed only by priming the other channel then repeating it.
    result['permanent_other_channel_cache']=(cached_other_channel(source).replace('self.cached.set(None);let bytes=self.sample.get();','let bytes=self.sample.get();'),'not_completed')
    return result
