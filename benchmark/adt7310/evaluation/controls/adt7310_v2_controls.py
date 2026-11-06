"""Host-only alternative and defect controls, not generated method outputs."""
def replace(source,old,new):
    assert source.count(old)==1,old
    return source.replace(old,new)


def spi_symbols(mode):
    if mode == 0: return 'IdleLow', 'SampleLeading'
    if mode == 3: return 'IdleHigh', 'SampleTrailing'
    raise ValueError('Unsupported ADT7310 control mode')


def legal_controls(source, mode=3):
    _, phase = spi_symbols(mode)
    wait=replace(source,'let result = self.transfer(&[0x40, 0], State::Status);','''self.state.set(State::Waiting);
        let ticks=(A::Frequency::frequency() as u64 * 700_000 + 999_999)/1_000_000;
        self.alarm.set_alarm(self.alarm.now(), A::Ticks::from_or_max(ticks).max(self.alarm.minimum_dt()));
        let result: Result<(),ErrorCode> = Ok(());''')
    wait=replace(wait,'if let Err(e) = self.transfer(&[0x40, 0], State::Status)', 'if let Err(e) = self.transfer(&[0x50, 0, 0], State::Data)')
    return {
        'nearest_rounding':replace(source,'raw * 100 / 128','(raw * 100 + raw.signum() * 64) / 128'),
        'reduced_fraction':replace(source,'raw * 100 / 128','raw * 25 / 32'),
        'comparator_mode':replace(source,'&[0x08, 0x80]','&[0x08, 0x90]'),
        'slower_spi':replace(source,f'ClockPhase::{phase}, 1_000_000',f'ClockPhase::{phase}, 500_000'),
        'no_identity_probe':replace(source,'self.transfer(&[0x58, 0], State::Identity)','self.transfer(&[0x08, 0x80], State::Configuration)'),
        'wait_without_status_poll':wait,
    }


def fault_controls(source, mode=3):
    polarity, phase = spi_symbols(mode)
    other_polarity, other_phase = spi_symbols(3 if mode == 0 else 0)
    pairs={
      'wrong_units':('raw * 100 / 128','raw / 128'),
      'wrong_scale':('raw * 100 / 128','raw * 100 / 16'),
      'wrong_sign':('i16::from_be_bytes','u16::from_be_bytes'),
      'wrong_endian':('i16::from_be_bytes','i16::from_le_bytes'),
      'wrong_resolution':('&[0x08, 0x80]','&[0x08, 0x00]'),
      'wrong_spi_mode':(f'ClockPolarity::{polarity}, ClockPhase::{phase}',f'ClockPolarity::{other_polarity}, ClockPhase::{other_phase}'),
      'wrong_spi_frequency':(f'ClockPhase::{phase}, 1_000_000',f'ClockPhase::{phase}, 2_000_000'),
      'short_temperature_transfer':('&[0x50, 0, 0]','&[0x50, 0]'),
      'fabricated_value':('raw * 100 / 128','0'),
      'duplicate_callback':('client.callback(Ok(value));','client.callback(Ok(value)); client.callback(Ok(value));'),
    }
    result={k:(replace(source,*v),'fail') for k,v in pairs.items()}
    result['missing_callback']=(replace(source,'client.callback(Ok(value));','let _ = (client, value);'),'not_completed')
    return result
