"""Legal native source forms must remain usable by isolated host scoring."""
import shutil
import tempfile
import unittest
from pathlib import Path

from benchmark.common.api import ROOT, owned, resolve_task
from benchmark.common.evaluation.tock.adapter import evaluate_driver
from benchmark.common.evaluation.nuttx.adapter import evaluate_behavior


class ScoringCompatibility(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='scoring-compatibility-', dir=ROOT/'run'))

    def tearDown(self):
        shutil.rmtree(self.work)

    def rust_source(self, directory):
        task = resolve_task('riot-tock-adt7310-spi-mode0')
        reference = owned(next(iter(task.spec['reference']['files'].values()))).read_text()
        parent = self.work/('directory' if directory else 'flat')
        helper = parent/'adt7310'
        helper.mkdir(parents=True)
        source = helper/'mod.rs' if directory else parent/'adt7310.rs'
        prefix = '' if directory else 'adt7310/'
        source.write_text(reference + '\nmod layout_probe;\n#[path="'+prefix+'extra.rs"] mod extra;\n'
                          + 'include!("'+prefix+'included.rs");\n')
        (helper/'layout_probe.rs').write_text('mod nested; const _: u8 = nested::VALUE;\n')
        (helper/'layout_probe').mkdir()
        (helper/'layout_probe/nested.rs').write_text('pub const VALUE: u8 = 17;\n')
        (helper/'extra.rs').write_text('const _: &str = include_str!("payload.txt");\n')
        (helper/'payload.txt').write_text('public candidate helper payload\n')
        (helper/'included.rs').write_text('const _: u8 = 3;\n')
        return task, parent/'adt7310.rs'

    def test_flat_and_directory_modules_preserve_relative_paths(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                task, source = self.rust_source(directory)
                report = evaluate_driver(task, source, ROOT, self.work/('eval-directory' if directory else 'eval-flat'))
                self.assertTrue(report['compiled'], report.get('reason', report.get('build')))
                self.assertTrue(report['software_behavior_pass'], report)
                self.assertEqual(report['candidate_source_layout'], 'directory_module' if directory else 'file_module')

    def test_ambiguous_module_roots_rejected(self):
        task, source = self.rust_source(False)
        (source.parent/'adt7310/mod.rs').write_text(source.read_text())
        report = evaluate_driver(task, source, ROOT, self.work/'ambiguous')
        self.assertEqual(report['status'], 'infrastructure_error')
        self.assertIn('Ambiguous', report['reason'])
        self.assertFalse(report['compiled'])

    def test_directory_and_helper_links_rejected(self):
        task, source = self.rust_source(True)
        helper = source.parent/'adt7310'
        (helper/'leak.txt').symlink_to(ROOT/'README.md')
        report = evaluate_driver(task, source, ROOT, self.work/'helper-link')
        self.assertEqual(report['status'], 'infrastructure_error')
        self.assertFalse(report['compiled'])
        (helper/'leak.txt').unlink()
        moved = source.parent/'linked-original'
        helper.rename(moved)
        helper.symlink_to(moved, target_is_directory=True)
        report = evaluate_driver(task, source, ROOT, self.work/'directory-link')
        self.assertEqual(report['status'], 'infrastructure_error')
        self.assertIn('symbolic link', report['reason'])
        self.assertFalse(report['compiled'])

    def test_nuttx_native_positional_initializer_and_public_headers(self):
        task = resolve_task('zephyr-nuttx-dht11-gpio')
        text = owned(next(iter(task.spec['reference']['files'].values()))).read_text()
        old = 'static const struct sensor_ops_s operations = {.activate=activate,.fetch=fetch};'
        self.assertEqual(text.count(old), 1)
        text = text.replace(old, 'static const struct sensor_ops_s operations = {NULL,NULL,activate,NULL,NULL,fetch,NULL,NULL,NULL,NULL,NULL,NULL,NULL};')
        self.assertEqual(text.count('nxmutex_init(&dev->lock);'), 1)
        text = text.replace('nxmutex_init(&dev->lock);', 'dev->lock=(mutex_t)NXMUTEX_INITIALIZER;')
        text = text.replace('#include <nuttx/config.h>', '#include <nuttx/config.h>\n#include <nuttx/uorb.h>\n#include <nuttx/clock.h>')
        source = self.work/'candidate'/task.editable_paths[0]
        source.parent.mkdir(parents=True)
        source.write_text(text)
        report = evaluate_behavior(task, self.work/'candidate', self.work/'nuttx-behavior', 'legal-native-abi')
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(report['backend'], 'frozen_nuttx_sim')
        self.assertTrue(report['cases'])
        self.assertTrue(all(c['status']=='pass' for c in report['cases']))


if __name__ == '__main__':
    unittest.main()
