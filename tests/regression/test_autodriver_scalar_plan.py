"""Generic scalar public-HIL plans; synthetic data only, no device answers."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from method.autodriver.implementation.system.contract.model import parse,Unknown
from method.autodriver.implementation.system.contract.expression import quantity
from method.autodriver.implementation.system.native.channels import SCALARS
from method.autodriver.implementation.system.native.plan import tock_plan


class ScalarPlanTests(unittest.TestCase):
    def test_all_declared_scalar_channels_and_multiple_acquisition_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);driver=root/'target/capsules/extra/src/synthetic.rs';driver.parent.mkdir(parents=True);driver.write_text('// synthetic placeholder, not compiled in this plan test\n')
            channels=['temperature','humidity','atmospheric_pressure','ambient_light','proximity','equivalent_co2','equivalent_tvoc']
            traits=['kernel::hil::sensors::'+value[0] for value in SCALARS.values()]+['kernel::hil::i2c::I2CClient','kernel::hil::time::AlarmClient']
            task={'requirements':{'functions':{'channels':channels}},'target':{'os':'tock','module':'synthetic','interface':{
                'traits':traits,'type':"Synthetic<'a,I,A>",'buffer_bytes':4,'constructor':"pub fn new(i2c: &'a I, alarm: &'a A, buffer: &'static mut [u8]) -> Self"}}}
            model=parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(WriteBytes([1]),Sequence(ReadFrame('a'),ReadFrame('b'),CommitValidatedSample(['a','b'])),recovery=None,reentry=None),frames={'a':FrameLayout(1,{'x':Field(0,8,'big')}),'b':FrameLayout(1,{'y':Field(0,8,'big')})})",'dbc')
            plan=tock_plan(task,model,root)
            self.assertEqual(set(plan['native_channels']),set(SCALARS));self.assertNotIn('@',plan['source'])
            self.assertEqual(len({case['case'] for case in plan['cases']}),len(plan['cases']))
            for symbol in ('PressureClient','AmbientLightClient','ProximityClient','AirQualityClient','co2_data_available','tvoc_data_available'):
                self.assertIn(symbol,plan['source'])
            self.assertEqual({case['frame'] for case in plan['cases']},{'a','b'})
            from method.autodriver.implementation.system.native.spi import native_plan
            spi_task=deepcopy(task);spi_task['requirements']['connection']={'spi':{'mode':0,'max_frequency_hz':1000000}}
            spi_task['target']['interface']['constructor']="pub fn new(spi: &'a S, alarm: &'a A, tx: &'static mut [u8], rx: &'static mut [u8]) -> Self"
            spi_task['target']['interface']['traits']=[trait.replace('kernel::hil::i2c::I2CClient','kernel::hil::spi::SpiMasterClient') for trait in traits]
            spi_model=parse("DeviceContract('synthetic',SPI(0,'selected'),FrozenPolicy(),{},Lifecycle(WriteBytes([1]),Sequence(ReadFrame('a'),ReadFrame('b'),CommitValidatedSample(['a','b'])),recovery=None,reentry=None),frames={'a':FrameLayout(1,{'x':Field(0,8,'big')}),'b':FrameLayout(1,{'y':Field(0,8,'big')})})",'dbc')
            spi=native_plan(spi_task,spi_model,root)
            self.assertEqual(set(spi['native_channels']),set(SCALARS));self.assertNotIn('@',spi['source'])
            self.assertEqual(len({case['case'] for case in spi['cases']}),len(spi['cases']))
            changed=deepcopy(task);changed['requirements']['functions']['channels'].append('magnetic_field_xyz')
            with self.assertRaisesRegex(Unknown,'required channel'):tock_plan(changed,model,root)
            changed=deepcopy(task);changed['target']['interface']['traits'].remove('kernel::hil::sensors::PressureDriver')
            with self.assertRaisesRegex(Unknown,'public trait'):tock_plan(changed,model,root)

    def test_scalar_units_have_exact_scale_without_converting_to_plain_counts(self):
        self.assertEqual(quantity(1,'ppm'),quantity(1000,'ppb'))
        self.assertEqual(quantity(1,'lux'),quantity(1,'lx'))
        self.assertNotEqual(quantity(1,'ppm').dimensions,quantity(1,'1').dimensions)
