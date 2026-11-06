"""A layout declaration does not make old calibration inputs current."""
import unittest

from method.autodriver.implementation.system.contract.check import check_contract


FRAMES="frames={'cal':FrameLayout(1,{'gain':Field(0,8,'big')}),'sample':FrameLayout(1,{'raw':Field(0,8,'big')})}"


class QuantityDataflowTests(unittest.TestCase):
    def test_old_frame_layout_is_not_a_committed_quantity_binding(self):
        text="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(ReadFrame('cal'),Sequence(ReadFrame('sample'),CommitValidatedSample('sample')),recovery=None,reentry=None),"+FRAMES+",quantities={'value':PhysicalQuantity('cal.gain*sample.raw','Pa')})"
        _,report=check_contract(text,'dbc')
        finding=next(f for f in report['findings'] if f['check']=='finite_quantity_input_binding')
        self.assertEqual(finding['status'],'unknown');self.assertIn('cal.gain',finding['detail'])
        self.assertTrue(any(f['check']=='numeric_definedness_over_declared_bounds' and f['status']=='satisfied' for f in report['findings']))

    def test_explicit_state_binding_survives_new_acquisition_without_a_validity_claim(self):
        text="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},FSM('cold',[State('cold'),State('ready'),State('done')],[Transition('cold',EventRef('initialize','logical_success'),'ready',action=ReadFrame('cal'),effects=[Assign('gain','cal.gain')]),Transition('ready',EventRef('acquire','logical_success'),'done',action=Sequence(ReadFrame('sample'),CommitValidatedSample('sample')))],variables={'gain':{'type':'int','initial':0}},accepting=['done']),"+FRAMES+",quantities={'value':PhysicalQuantity('gain*sample.raw','Pa',requires=['valid'])})"
        _,report=check_contract(text,'dbc')
        finding=next(f for f in report['findings'] if f['check']=='finite_quantity_input_binding')
        self.assertEqual(finding['status'],'unknown');self.assertIn('valid',finding['detail'])
        self.assertNotIn('gain',finding['detail'])
        _,bound=check_contract(text.replace(",requires=['valid']",''),'dbc')
        finding=next(f for f in bound['findings'] if f['check']=='finite_quantity_input_binding')
        self.assertEqual(finding['status'],'satisfied')

    def test_state_domains_follow_decoded_aliases_and_keep_boolean_truth_unassumed(self):
        from method.autodriver.implementation.system.contract.model import parse
        from method.autodriver.implementation.system.contract.domains import domains_from_frames,domains_from_state
        text="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},FSM('cold',[State('cold'),State('done')],[Transition('cold',EventRef('initialize','logical_success'),'done',action=ReadFrame('cal'),effects=[Assign('gain','cal.gain'),Assign('copy','gain'),Assign('growing','growing+1'),Assign('initialized','True')])],variables={'gain':{'type':'int','initial':0},'copy':{'type':'int','initial':-1},'growing':{'type':'int','initial':0},'initialized':{'type':'bool','initial':False}},accepting=['done']),"+FRAMES+")"
        root=parse(text,'dbc');domains=domains_from_state(root,domains_from_frames(root))
        self.assertEqual(domains['gain']['bounds'],(0,255));self.assertEqual(domains['copy']['bounds'],(-1,255))
        self.assertNotIn('growing',domains);self.assertEqual(domains['initialized']['bounds'],(False,True))
        malformed=text.replace("Assign('growing','growing+1')","Assign('growing','initialized')").replace("Assign('copy','gain')","Assign('copy','growing')")
        root=parse(malformed,'dbc');domains=domains_from_state(root,domains_from_frames(root))
        self.assertNotIn('copy',domains);self.assertNotIn('growing',domains)

    def test_proof_uses_dependency_closure_and_resource_limit_remains_unknown(self):
        from method.autodriver.implementation.system.contract.model import node
        from method.autodriver.implementation.system.contract.domains import numeric_domain
        from method.autodriver.implementation.system.contract.solver import prove
        domains={'unused'+str(i):{'type':'int','bounds':(0,255)} for i in range(200)}
        domains['raw']={'type':'int','bounds':(0,255)}
        value=node('PhysicalQuantity',expression='helper/2',unit='Pa',requires=('raw>=0',))
        self.assertEqual(numeric_domain(value,domains,definitions={'helper':'raw+1'})['status'],'satisfied')
        self.assertEqual(prove('True',{name:'int' for name in domains})['status'],'unknown')

    def test_event_anchors_resolve_exactly_in_the_same_global_graph(self):
        def report(anchor):
            text="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(WriteBytes([2],name='reset'),WaitFrom("+anchor+",'1 ms'),recovery=None,reentry=None))"
            return check_contract(text,'dbc')[1]
        for anchor in ("EventRef('initialization.reset','physical_success')","EventRef('initialization','physical_start')"):
            result=report(anchor)
            self.assertTrue(any(f['check']=='declared_anchor_event' and f['status']=='violated' for f in result['findings']))
        result=report("EventRef('reset','physical_success')")
        self.assertFalse(any(f['check']=='declared_anchor_event' and f['status']=='violated' for f in result['findings']))
