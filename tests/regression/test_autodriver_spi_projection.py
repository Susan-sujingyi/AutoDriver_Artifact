"""Offline SPI transaction projection, synthetic traces only."""
import unittest

from method.autodriver.implementation.system.contract.model import parse


from method.autodriver.implementation.system.native.plan import Projection


class SpiProjectionTests(unittest.TestCase):
    def trace(self,*,header=18,short=False):
        contract=parse("DeviceContract('synthetic',SPI(0,'selected'),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence(Transfer([WriteSegment([18]),ReadSegment(2,'data')],'CS end'),CommitValidatedSample('data')),on_failure=FailCurrentOperation(True),recovery=None,reentry=None),frames={'data':FrameLayout(2,{'raw':Field(0,16,'big')})})",'dbc')
        p=Projection(contract)
        def feed(phase,tick,**fields):p.feed(dict(phase=phase,tick=tick,frequency=1000,**fields))
        feed('request_boundary',0,instance='one');feed('submit',0,operation=1,write=[header,7,8],read_len=3)
        feed('physical_start',0,operation=1);feed('physical_success',1,operation=1)
        feed('callback_success',1,operation=1,data=[99,1] if short else [99,1,2]);feed('output',2,failed=False)
        return p.result(),p.machine

    def test_full_spi_callback_projects_only_declared_read_segment(self):
        report,machine=self.trace();self.assertEqual(report['status'],'satisfied')
        self.assertTrue(machine.result()['operation_succeeded']);self.assertEqual(machine.frames['data'].fields['raw'],258)

    def test_wrong_header_and_short_callback_cannot_commit_success(self):
        for args in ({'header':19},{'short':True}):
            report,machine=self.trace(**args);self.assertEqual(report['status'],'violated');self.assertIsNone(machine.sample)


if __name__=='__main__':unittest.main()
