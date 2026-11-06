"""Synthetic SPI mode/resources/encoding counterexamples, no sensor fixture."""
import json
import unittest

from tests.regression.test_autodriver_prefix import contract
from method.autodriver.implementation.system.contract.model import parse,node
from method.autodriver.implementation.system.native.spi import evaluate


class SpiNativeTests(unittest.TestCase):
    def observe(self,*,mode=0,value=2):
        root=contract();fields=dict(root.fields)
        fields['quantities']={'physical':node('PhysicalQuantity',expression='sample.raw / 4',unit='degC')};root=node('DeviceContract',**fields)
        fp=parse("FrameworkProfile('synthetic','snapshot',capabilities={'spi':APIContract('SpiMasterDevice::read_write_bytes',{},accepted=HoldsBufferUntilCompletion(),rejected=ReturnsErrorAndBuffer(),completion=Callback('done',{},ReturnsBufferToClient()))},outputs={'temperature':SensorInterface('read',encoding=OutputEncoding('i32','degC',scale=0.5))})",'fp')
        bp=parse("BindingPlan(Ref('d'),Ref('f'),Ref('s'),'synthetic',primitive_bindings={'delivery':DeliverRequestedQuantity(FromDBC('quantities.physical'),FromFP('outputs.temperature.encoding'),Rounding('nearest_ties_even'))})",'bp')
        events=[{'phase':'case_begin','case':'sample'}]
        def event(phase,tick,**kw):events.append(dict(phase=phase,tick=tick,frequency=1000,**kw))
        def io(id,tick,write,data):
            read=len(data);resources={'write_buffer':0,'read_buffer':1 if read else None}
            event('submit',tick,operation=id,write=write,read_len=read,mode=mode,rate=500000,**resources)
            event('accepted',tick,operation=id);event('cs_assert',tick,operation=id);event('physical_start',tick,operation=id)
            event('physical_success',tick+1,operation=id);event('cs_release',tick+1,operation=id)
            event('callback_success',tick+1,operation=id,data=data,**resources)
        io(1,0,[3,0],[0,136]);io(2,2,[9],[]);event('request_boundary',4,instance='one');io(3,4,[5,0],[0,5])
        event('output',6,channel='temperature',value=value,failed=False);events.append({'phase':'case_end'})
        return evaluate(root,fp,{'cases':[{'case':'sample'}],'configuration':{'mode':0,'max_frequency_hz':1000000},'scope':'synthetic trace only'},'\n'.join(map(json.dumps,events)),bp)

    def test_known_public_resource_and_numeric_bindings_are_executed(self):
        case=self.observe()['cases'][0]
        self.assertEqual(case['violations'],[]);self.assertEqual(case['resources']['status'],'satisfied')
        self.assertEqual(case['numeric_points'][0]['stored'],2)

    def test_wrong_spi_mode_and_wrong_rounding_are_separate_counterexamples(self):
        self.assertTrue(any('mode/rate' in x for x in self.observe(mode=3)['cases'][0]['violations']))
        self.assertTrue(any('numeric output differs' in x for x in self.observe(value=3)['cases'][0]['violations']))
