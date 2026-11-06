"""Benchmark material policy, not a driver-generation heuristic.

The named holdout population and reviewed related implementations are fixed for
all G/E/A tasks. Policy changes change the material/knowledge identity.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil

DEVICES = ('apds9960', 'bme280', 'ccs811', 'isl29035', 'lps22hb', 'bmp388',
           'adxl362', 'adt7310', 'bh1750', 'hc_sr04', 'lsm6dso', 'max30102',
           'bmm150', 'lsm303dlhc', 'mpu6050', 'dht20', 'dht11', 'sht3x', 'dps310')
ALIASES = ('hcsr04', 'sht30', 'sht31', 'sht35', 'lsm6dsox')
# Evidence and the scope of this conservative family holdout are in
# docs/MATERIAL_BOUNDARY.md. These are corpus decisions, not compatibility claims.
RELATED = ('bmp280', 'lsm6dsoxtr', 'lsm303agr', 'lsm303xx', 'sht4x', 'lps25hb')
EXCLUDED = frozenset(DEVICES + ALIASES + RELATED)


def policy():
    value = {'schema': 'driverbench.material-policy/v1',
             'population': list(DEVICES), 'aliases': list(ALIASES),
             'related_holdout': list(RELATED),
             'target_scope': 'all planned devices for every G/E/A run',
             'generic_hil_and_syscall_wrappers': 'retained',
             'board_policy': 'retain current task wiring; remove unrelated held-out dependencies'}
    value['sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def blocked(path):
    return any(re.search(r'(^|[^a-z0-9])' + re.escape(name) + r'($|[^a-z0-9])',
                         part.lower().replace('-', '_'))
               for part in Path(path).parts for name in EXCLUDED)


def example_files(target):
    return sorted(p.relative_to(target).as_posix()
                  for p in (Path(target) / 'capsules/extra/src').glob('*.rs')
                  if not blocked(p.relative_to(target)) and re.search(r'\bsensors::', p.read_text()))


def prune_board(text, current):
    """Remove dependency-closed unused wiring from the pinned public board.

    This is a bounded fixture transformation, not a Rust semantic analyzer.
    Any remaining unresolved removed identifier is rejected, and native builds
    validate the resulting selected-device board separately.
    """
    absent = EXCLUDED - {current}
    ref = re.compile(r'\b(?:components|capsules_extra)::(' + '|'.join(sorted(absent)) + r')::')
    aliases = list(re.finditer(r'^type (\w+)\s*=.*?;\n', text, re.M | re.S))
    statements = list(re.finditer(r'^    let (\w+)\s*=\s*components::.*?;\n', text, re.M | re.S))
    removed = set()
    spans = set()
    while True:
        before = len(removed)
        for m in aliases + statements:
            if ref.search(m[0]) or any(re.search(r'\b' + re.escape(n) + r'\b', m[0]) for n in removed):
                removed.add(m[1]); spans.add((m.start(), m.end()))
        if len(removed) == before: break
    for start, end in sorted(spans, reverse=True): text = text[:start] + text[end:]
    # Platform fields, syscall dispatch and struct initializer shorthand are
    # public wiring only. Remove dependants of the discarded declarations.
    fields = set()
    for m in re.finditer(r'^    (\w+):\s*&\x27static (\w+),\n', text, re.M):
        if m[2] in removed or m[1] in removed: fields.add(m[1])
    removed |= fields
    text = ''.join(line for line in text.splitlines(keepends=True)
        if not (any(re.search(r'\b' + re.escape(n) + r'\b', line) for n in removed)
                and (re.match(r'^    \w+:', line) or '=> f(Some(self.' in line
                     or re.match(r'^        \w+,\s*$', line))))
    if ref.search(text) or any(re.search(r'\b' + re.escape(n) + r'\b', re.sub(r'//[^\n]*', '', text)) for n in removed):
        raise ValueError('Unsupported board dependency shape: ' + repr([line for line in text.splitlines() if ref.search(line) or any(re.search(r'\b' + re.escape(n) + r'\b', re.sub(r'//[^\n]*', '', line)) for n in removed)]))
    return text, sorted(removed)


def sanitize_target(target, current):
    """Apply after the unchanged per-task public board patch, before exposure."""
    target = Path(target)
    removed = []
    # Device-specific docs and alternate copies with the reviewed names are also
    # excluded. Current task component contains only the allowed public wiring.
    for p in sorted(target.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        rel = p.relative_to(target)
        if not blocked(rel): continue
        if rel.as_posix() == f'boards/components/src/{current}.rs': continue
        if p.is_file(): removed.append(rel.as_posix()); p.unlink()
        elif p.is_dir(): shutil.rmtree(p)
    for base in ('capsules/extra/src', 'boards/components/src'):
        lib = target / base / 'lib.rs'
        if lib.exists():
            lib.write_text(re.sub(r'^pub mod (\w+);\n',
                lambda m: '' if m[1] in EXCLUDED and m[1] != current else m[0], lib.read_text(), flags=re.M))
    board = target / 'boards/nordic/nrf52840dk/src/main.rs'
    pruned = []
    if board.exists():
        text, pruned = prune_board(board.read_text(), current); board.write_text(text)
    remaining = [p.relative_to(target).as_posix() for p in target.rglob('*')
                 if p.is_file() and blocked(p.relative_to(target))
                 and p.relative_to(target).as_posix() != f'boards/components/src/{current}.rs']
    if remaining: raise ValueError('Held-out files survived material filtering')
    return {'policy': policy(), 'removed_files': sorted(removed), 'removed_board_bindings': pruned}
