"""In-process checkpoint hash reuse, bound to lstat identity including ctime.

This is not a release proof or persistent cache. It enumerates all paths on
each call and hashes new/changed files. Final candidate evaluation still uses
the independent full inventory. An actively changing file is refused.
"""
import os
from pathlib import Path
import stat

from benchmark.common.api import digest


def stamp(path):
    st = path.lstat()
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class CheckpointInventory:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.cache = {}

    def read(self):
        result = {}
        active = {}
        for base, directories, files in os.walk(self.root, followlinks=False):
            for name in directories + files:
                path = Path(base) / name
                key = path.relative_to(self.root).as_posix()
                before = stamp(path)
                if stat.S_ISLNK(before[2]):
                    if not path.resolve().is_relative_to(self.root):
                        raise ValueError('Unapproved link: ' + str(path))
                    value = {'symlink':os.readlink(path)}
                elif stat.S_ISREG(before[2]):
                    cached = self.cache.get(key)
                    value = cached[1] if cached and cached[0] == before else {
                        'sha256':digest(path), 'executable':bool(before[2] & 0o111)}
                    active[key] = (before, value)
                elif stat.S_ISDIR(before[2]):
                    continue
                else:
                    raise ValueError('Unsupported candidate file: ' + key)
                if stamp(path) != before:
                    raise ValueError('Candidate changed during checkpoint: ' + key)
                result[key] = value
        self.cache = active
        return dict(sorted(result.items()))
