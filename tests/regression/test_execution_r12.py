"""Deadline cleanup tests; no providers or hardware."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmark.common.api import ROOT, load, save
from tests.common.execution import process as supervisor
from tests.common.sandbox import tool_roots


class DeadlineCleanup(unittest.TestCase):
    def test_real_timeout_reaps_detached_sigterm_ignoring_descendant(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'run') as tmp:
            base=Path(tmp); pidfile=base/'detached.pid'
            code=("import os,signal,time;from pathlib import Path;"
                  "pid=os.fork();"
                  f"Path({str(pidfile)!r}).write_text(str(pid)) if pid else None;"
                  "os.setsid() if not pid else None;"
                  "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(120)")
            result=supervisor.execute([str(ROOT/'.venv/bin/python'),'-c',code],
                cwd=base,readonly=tool_roots(ROOT),writable=[base],
                environment={'PATH':'/usr/bin:/bin'},output=base/'process',timeout=3)
            self.assertTrue(pidfile.exists(),result)
            self.assertEqual(result['status'],'not_completed',result)
            self.assertTrue(result['timed_out'])
            self.assertEqual(result['cleanup']['status'],'pass',result)
            self.assertEqual(load(base/'process/process.json'),result)
            self.assertFalse((base/'process/.launch.json').exists())
            with self.assertRaises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)

    def test_cleanup_over_old_ten_second_bound_preserves_deadline(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'run') as tmp:
            base=Path(tmp); output=base/'process'; child=Mock(pid=99999999,returncode=None)
            receipt_fd=[]
            child.poll.return_value=None
            child.communicate.side_effect=subprocess.TimeoutExpired(['fixture'],1)
            def spawn(*args,**kwargs):
                receipt_fd.append(os.dup(kwargs['pass_fds'][-1]))
                return child
            def reaped(*,timeout):
                self.assertGreater(timeout,10)
                os.write(receipt_fd[0],b'{"status":"pass","seconds":12}')
                os.close(receipt_fd[0])
                child.returncode=130
            child.wait.side_effect=reaped
            with patch.object(supervisor.subprocess,'Popen',side_effect=spawn):
                result=supervisor.execute(['fixture'],cwd=base,readonly=[],writable=[],
                    environment={},output=output,timeout=1)
            self.assertEqual(result['status'],'not_completed',result)
            self.assertTrue(result['timed_out'])
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=supervisor.CLEANUP_GRACE_SECONDS)

    def test_stuck_worker_does_not_throw_secondary_timeout_or_claim_cleanup(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'run') as tmp:
            base=Path(tmp); child=Mock(pid=99999999,returncode=None)
            child.poll.return_value=None
            child.communicate.side_effect=subprocess.TimeoutExpired(['fixture'],1)
            child.wait.side_effect=subprocess.TimeoutExpired(['fixture'],1)
            with patch.object(supervisor.subprocess,'Popen',return_value=child), \
                 patch.object(supervisor.os,'killpg') as kill:
                result=supervisor.execute(['fixture'],cwd=base,readonly=[],writable=[],
                    environment={},output=base/'process',timeout=1)
            kill.assert_called_once_with(child.pid,signal.SIGKILL)
            self.assertEqual(result['status'],'infrastructure_error')
            self.assertTrue(result['timed_out'])
            self.assertEqual(result['shutdown']['reason'],'worker_survived_sigkill')
            self.assertNotEqual(result['cleanup']['status'],'pass')

    def test_missing_and_partial_cleanup_fail_closed(self):
        for payload,status in [(b'', 'missing'),(b'{','invalid'),(b'[]','invalid'),
                               (b'{"status":"pass"}','pass')]:
            r,w=os.pipe()
            try:
                if payload:os.write(w,payload)
                self.assertEqual(supervisor.receive_cleanup(r)['status'],status)
            finally:os.close(r);os.close(w)

    def test_agent_cannot_forge_host_cleanup_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'run') as tmp:
            base=Path(tmp)
            # A hostile file is ignored: only the private subreaper pipe can
            # certify cleanup. Even a mock empty pipe must fail closed.
            child=Mock(pid=99999999,returncode=0)
            child.poll.return_value=0
            def done(**kwargs):save(base/'process/cleanup.json',{'status':'pass'})
            child.communicate.side_effect=done
            with patch.object(supervisor.subprocess,'Popen',return_value=child):
                result=supervisor.execute(['fixture'],cwd=base,readonly=[],writable=[],
                    environment={},output=base/'process',timeout=1)
            self.assertEqual(result['status'],'infrastructure_error')
            self.assertNotEqual(result['cleanup']['status'],'pass')

if __name__=='__main__':unittest.main()
