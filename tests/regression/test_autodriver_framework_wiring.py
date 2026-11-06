"""Board framework dependencies contain no device implementation snippets."""
import unittest

from method.autodriver.implementation.system.framework_wiring import construction_slice
from method.autodriver.implementation.system.contract.model import Unknown


class BoardWiringTests(unittest.TestCase):
    def test_constructor_inputs_and_startup_effects_have_complete_source_spans(self):
        source=b'''unsafe fn start() {
 let peripherals = ChipPeripherals::new();
 let rtc = &peripherals.rtc;
 let _ = rtc.start();
 let alarm = AlarmMuxComponent::new(rtc).finalize(storage);
 let bus = BusMuxComponent::new(&peripherals.bus, None).finalize(bus_storage);
 peripherals.bus.configure(pins);
 peripherals.unrelated.configure(other_pins);
 let raw = storage!(namespace::radio::SIZE);
 radio.set_device_protocol(device_driver);
 let device = ExistingDeviceComponent::new(bus, alarm, 123).finalize(device_storage);
 let unrelated = secret_device_protocol();
}
fn device_answer() { let answer = 99; }
'''
        result=construction_slice(source,{'AlarmMuxComponent','BusMuxComponent'})
        statements='\n'.join(row['source'] for row in result['statements'])
        for required in ('ChipPeripherals::new','rtc.start','AlarmMuxComponent::new','BusMuxComponent::new','bus.configure'):
            self.assertIn(required,statements)
        for forbidden in ('ExistingDeviceComponent','secret_device_protocol','device_answer','unrelated.configure','set_device_protocol'):
            self.assertNotIn(forbidden,statements)
        for row in result['statements']:
            fragment=b'\n'.join(source.splitlines()[row['start_line']-1:row['end_line']]).decode()
            self.assertIn(row['source'],fragment)

    def test_conditions_shadowing_and_bounds_are_never_silently_resolved(self):
        source=b'''#[cfg(feature="board")]
fn start() {
 let clock = clock_a();
 let clock = clock_b();
 if enabled { let alarm = AlarmMuxComponent::new(clock).finalize(storage); }
}
'''
        result=construction_slice(source,{'AlarmMuxComponent'})
        self.assertTrue(result['gaps']);self.assertEqual(len(result['statements']),1)
        self.assertEqual({x['kind'] for x in result['statements'][0]['conditions']},{'if_expression','cfg'})
        with self.assertRaises(Unknown):construction_slice(source,{'AlarmMuxComponent'},maximum_statements=0)
        nested=b'fn start(){ let closure = || { let alarm=AlarmMuxComponent::new(clock); }; }'
        self.assertEqual(construction_slice(nested,{'AlarmMuxComponent'})['statements'],[])
        branches=b'''fn start() { #[cfg(feature="clock")] if selected {
 let a=AlarmMuxComponent::new(first);
} else { let b=AlarmMuxComponent::new(second); } }
#[cfg(test)] fn example(){let c=AlarmMuxComponent::new(test_clock);}
'''
        rows=construction_slice(branches,{'AlarmMuxComponent'})['statements']
        self.assertEqual(len(rows),2)
        self.assertEqual([next(c['branch'] for c in row['conditions'] if c['kind']=='if_expression') for row in rows],['consequence','alternative'])
        self.assertTrue(all(any(c['kind']=='cfg' for c in row['conditions']) for row in rows))
