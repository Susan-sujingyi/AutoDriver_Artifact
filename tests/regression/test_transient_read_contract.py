"""Read success/error and output ownership for the shared host observer."""
from pathlib import Path
import resource
import subprocess
import tempfile
import unittest
from benchmark.common.api import ROOT

HEADER = ROOT / 'benchmark/common/evaluation/nuttx/sim/app/recovery_observer.h'
SOURCE = r'''
#include <errno.h>
#include <stdlib.h>
#include "recovery_observer.h"
ssize_t read(int fd, void *buffer, size_t count)
{
  unsigned char *p=buffer;
  if (fd==1 || fd==5) {errno=EIO;return -1;}
  if (fd==2) return count-1;
  if (fd==3) {p[0]=0;errno=EIO;return -1;}
  if (fd==4) p[count]=0;
  memset(buffer,42,count);
  return count;
}
int main(int argc,char **argv)
{
  assert(argc==2);
  int mode=atoi(argv[1]);
  unsigned char event[16];memset(event,0xa5,sizeof(event));
  ssize_t result=bench_observe_read(mode,event,8,sizeof(event),mode!=5);
  if (mode==1) assert(result<0 && event[0]==0xa5);
  else assert(result==8 && event[0]==42 && event[8]==0xa5);
  int pending=mode==6 ? -EIO:0;
  bench_finish_transient(&pending);
  assert(!pending);
  return 0;
}
'''

class TransientReadContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='observer-contract-', dir=ROOT/'run')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        (cls.work/'recovery_observer.h').write_bytes(HEADER.read_bytes())
        (cls.work/'check.c').write_text(SOURCE)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','check.c','-o','check'],
                       cwd=cls.work, check=True, capture_output=True, text=True)

    def observe(self, mode, passes):
        def no_core():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        result = subprocess.run([str(self.work/'check'), str(mode)], cwd=self.work,
                                capture_output=True, text=True, preexec_fn=no_core)
        if passes:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertLess(result.returncode, 0, result.stderr)
            self.assertIn('Assertion', result.stderr)
        return result

    def test_complete_event(self):
        self.observe(0, True)

    def test_propagated_error_preserves_output(self):
        self.assertIn('propagated_error', self.observe(1, True).stdout)

    def test_short_success_is_rejected(self):
        self.observe(2, False)

    def test_error_cannot_publish_partial_data(self):
        self.observe(3, False)

    def test_write_past_requested_event_is_rejected(self):
        self.observe(4, False)

    def test_normal_request_cannot_pass_with_error(self):
        self.observe(5, False)

    def test_unconsumed_fault_is_explicit(self):
        self.assertIn('consumed=0', self.observe(6, True).stdout)

if __name__ == '__main__':
    unittest.main()
