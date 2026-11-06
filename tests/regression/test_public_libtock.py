"""Public userspace support must not reintroduce an embedded target kernel."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from benchmark.common.api import inventory
from benchmark.common.target.prepare import copy_public_libtock, verify_public_support, verify_baseline


class PublicLibtockTests(unittest.TestCase):
    def test_only_root_kernel_is_excluded(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, dest = root / 'source', root / 'public'
            files = {'tock/capsules/sensor.rs': 'hidden implementation',
                     'Cargo.toml': '[workspace]\n', 'LICENSE': 'license',
                     '.cargo/config.toml': '[net]\noffline=true\n',
                     'runtime/src/lib.rs': 'runtime',
                     'examples/tock/readme': 'not the root kernel'}
            for name, text in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (source / 'runtime/src/lib.rs').chmod(0o755)
            before = inventory(source)
            copy_public_libtock(source, dest)
            self.assertEqual(inventory(source), before)
            self.assertEqual(inventory(dest), {n: v for n, v in before.items()
                                              if not n.startswith('tock/')})
            self.assertFalse((dest / 'tock').exists())
            with self.assertRaises(FileExistsError):
                copy_public_libtock(source, dest)

    def test_reintroduced_directory_file_and_dangling_link_are_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            embedded = root / 'support/libtock-rs/tock'
            embedded.parent.mkdir(parents=True)
            task = SimpleNamespace(os='tock')
            verify_public_support(task, root)
            for kind in ('directory', 'file', 'link'):
                if kind == 'directory': embedded.mkdir()
                elif kind == 'file': embedded.write_text('kernel archive')
                else: embedded.symlink_to(root / 'missing-kernel')
                with self.assertRaisesRegex(ValueError, 'embedded tock'):
                    verify_public_support(task, root)
                # Reject even before loading a supplied/forged baseline receipt.
                with self.assertRaisesRegex(ValueError, 'embedded tock'):
                    verify_baseline(task, root)
                if kind == 'directory': embedded.rmdir()
                else: embedded.unlink()

    def test_no_root_links_to_original_dependency(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'runtime').symlink_to(root / 'outside')
            with self.assertRaisesRegex(ValueError, 'root link'):
                copy_public_libtock(source, root / 'public')


if __name__ == '__main__':
    unittest.main()
