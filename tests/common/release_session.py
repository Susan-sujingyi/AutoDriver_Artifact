"""Host-only, group-scoped release admission; never compile source OS code.

One successful full content verification is shared by workers on this host.
Every subsequent admission scans the SAME population using lstat identities,
including ctime (restoring mtime does not conceal a write). This trusts the
host filesystem/administrator, as does the release manifest; agents cannot
write the release tree or this receipt. A changed tree fails closed, rather
than silently refreshing a receipt. No receipt survives relocation/reboot.
"""
import fcntl
import json
import os
from pathlib import Path
import stat
import time

from benchmark.common.api import digest, identity, load, relative, save


from benchmark.common.freeze_scope import paths, verify_scope, METHODS

def _metadata(path):
    st = path.lstat()
    if stat.S_ISDIR(st.st_mode):
        # Population below is enumerated; excluded pycache/knowledge writes
        # need not invalidate otherwise unchanged directories.
        return [st.st_dev, st.st_ino, st.st_mode]
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
        raise ValueError('Unsupported frozen material type: ' + str(path))
    return [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns,
            os.readlink(path) if stat.S_ISLNK(st.st_mode) else None]


def snapshot(root, manifest, scope=None):
    root = Path(root)
    names = paths(root, [scope] if scope else None)
    return {name: _metadata(root/name) for name in [*names, 'manifest.json']}


def _binding(root):
    if (root/'manifest.json').is_symlink():
        raise ValueError('Linked release manifest')
    return {'root': str(root.resolve()), 'manifest_sha256': digest(root / 'manifest.json'),
            'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'implementation_sha256': digest(Path(__file__))}


class ReleaseSession:
    def __init__(self, root, directory, *, verifier=None, method=None, scope=None):
        self.method, self.scope = method, scope
        if method is not None and method not in METHODS:
            raise ValueError('Unknown method scope')
        self.root = Path(root).resolve()
        self.directory = Path(directory) / scope if scope else Path(directory)
        if not self.directory.resolve().is_relative_to(self.root / 'result'):
            raise ValueError('Release verification receipt must remain host-side in result/')
        for path in (self.directory, *self.directory.parents):
            if path == self.root:
                break
            if path.is_symlink():
                raise ValueError('Linked release receipt directory')
        self.directory.mkdir(parents=True, exist_ok=True)
        host = identity({'root':str(self.root), 'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()})[:24]
        self.receipt = self.directory / ('release-verification-' + host + '.json')
        self.verifier = verifier

    def verify(self):
        if self.method is not None:
            results = []
            children = []
            for scope in ('common', self.method):
                child = ReleaseSession(self.root, self.directory, scope=scope)
                results.append(child.verify()); children.append(child)
            self.last = {'mode': '+'.join(c.last['mode'] for c in children),
                         'seconds': sum(c.last['seconds'] for c in children),
                         'entries': sum(c.last['entries'] for c in children)}
            return {'status':'pass', 'release_identity':results[0]['release_identity'],
                    'dependency_identities':{v['scope']:v['scope_identity'] for v in results}, 'source_compilations':0}
        start = time.monotonic()
        lock = self.directory / 'release-verification.lock'
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            binding = _binding(self.root)
            manifest = json.loads((self.root / 'manifest.json').read_text())
            current = snapshot(self.root, manifest, self.scope)
            if self.receipt.is_symlink():
                raise ValueError('Linked release verification receipt')
            if self.receipt.exists():
                record = json.loads(self.receipt.read_text())
                if (record.get('schema') != 'autodriver.release-session/v1' or record.get('binding') != binding or
                        record.get('receipt_identity') != identity({k:v for k,v in record.items() if k != 'receipt_identity'})):
                    raise ValueError('Release verification session changed; use a new session after review')
                if record['metadata'] != current:
                    names = sorted(k for k in current.keys() | record['metadata'].keys()
                                   if current.get(k) != record['metadata'].get(k))
                    raise ValueError('Frozen release changed since full verification: ' + ', '.join(names[:5]))
                result = record['verification']
                mode = 'metadata_reuse'
            else:
                from benchmark.common.release import verify_release
                result = self.verifier() if self.verifier else (verify_scope(self.scope, self.root) if self.scope else verify_release())
                if current != snapshot(self.root, manifest, self.scope) or binding != _binding(self.root):
                    raise ValueError('Release changed during full verification')
                record = {'schema':'autodriver.release-session/v1', 'binding':binding,
                          'verification':result, 'metadata':current, 'created':time.time()}
                record['receipt_identity'] = identity(record)
                save(self.receipt, record)
                self.receipt.chmod(0o600)
                mode = 'full_hash'
            if result.get('release_identity') != manifest['release_identity']:
                raise ValueError('Release verification identity mismatch')
            self.last = {'mode':mode, 'seconds':round(time.monotonic()-start, 3), 'entries':len(current)}
            return result
