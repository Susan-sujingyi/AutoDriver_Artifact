"""Synthetic pair/cross-slot ownership counterexamples, no candidate claim."""
import unittest

from method.autodriver.implementation.system.contract.model import parse,ContractError

from method.autodriver.implementation.system.contract.resource_set import BufferSetMonitor


def api(known=True):
    effects='accepted=HoldsBufferUntilCompletion(),rejected=ReturnsErrorAndBuffer(),completion=Callback(\'done\',{},ReturnsBufferToClient())' if known else ''
    return parse("FrameworkProfile('synthetic','snapshot',capabilities={'api':APIContract('transfer',{},"+effects+")})",'fp')['capabilities']['api']


class BufferTests(unittest.TestCase):
    def event(self,monitor,phase,instance='one',**kwargs):monitor.feed(operation='bus',instance=instance,phase=phase,**kwargs)

    def test_both_buffers_return_and_write_only_optional_receive(self):
        for buffers in ({'write':'a','read':'b'},{'write':'a'}):
            for terminal in ('rejected','callback_success','callback_failure'):
                with self.subTest(buffers=buffers,terminal=terminal):
                    monitor=BufferSetMonitor(api());self.event(monitor,'submit',buffers=buffers)
                    if terminal!='rejected':self.event(monitor,'accepted')
                    self.event(monitor,terminal,returned_buffers=buffers)
                    self.assertEqual(monitor.result()['status'],'satisfied');self.assertEqual(monitor.result()['pending_requests'],0)

    def test_one_missing_or_swapped_buffer_and_cross_slot_reuse_fail(self):
        for returned in ({'write':'a'},{'write':'b','read':'a'}):
            monitor=BufferSetMonitor(api());self.event(monitor,'submit',buffers={'write':'a','read':'b'});self.event(monitor,'accepted')
            with self.assertRaises(ContractError):self.event(monitor,'callback_success',returned_buffers=returned)
        monitor=BufferSetMonitor(api());self.event(monitor,'submit',buffers={'write':'a','read':'b'});self.event(monitor,'accepted')
        with self.assertRaises(ContractError):self.event(monitor,'submit',instance='two',buffers={'write':'c','read':'a'})

    def test_unknown_effects_and_pending_requests_remain_unknown(self):
        monitor=BufferSetMonitor(api(False));self.event(monitor,'submit',buffers={'write':'a','read':'b'});self.event(monitor,'accepted')
        self.assertEqual(monitor.result()['pending_requests'],1)
        self.event(monitor,'callback_success',returned_buffers={'write':'a','read':'b'})
        self.assertEqual(monitor.result()['status'],'unknown')


if __name__=='__main__':unittest.main()
