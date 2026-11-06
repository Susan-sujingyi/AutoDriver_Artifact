"""Check the final nRF52840 image's NSH application registration, offline."""
from pathlib import Path
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile

from benchmark.common.api import digest

REQUIRED_CONFIG = ('BUILD_FLAT', 'BUILTIN', 'NSH_BUILTIN_APPS', 'SYSTEM_SENSOR_SAMPLE')


def verify_command(elf: Path, binary: Path, config: Path, *, task_id: str,
                   build_id: str) -> dict:
    lines = set(config.read_text().splitlines())
    missing = [key for key in REQUIRED_CONFIG if 'CONFIG_' + key + '=y' not in lines]
    if missing:
        raise ValueError('NSH application configuration missing: ' + ', '.join(missing))
    if 'CONFIG_SCHED_USER_IDENTITY=y' in lines:
        raise ValueError('Unsupported builtin table ABI: user identity enabled')
    with elf.open('rb') as stream:
        image = ELFFile(stream)
        if image.elfclass != 32 or not image.little_endian or image['e_machine'] != 'EM_ARM':
            raise ValueError('Expected a 32-bit little-endian ARM image')
        symtab = image.get_section_by_name('.symtab')
        symbols = {s.name: s for s in symtab.iter_symbols()
                   if s['st_shndx'] != 'SHN_UNDEF'} if symtab else {}
        required = ('sensor_sample_main', 'g_builtins', 'g_builtin_count', 'builtin_isavail')
        if any(name not in symbols for name in required):
            raise ValueError('Final ELF lacks NSH application symbols')

        def read(address, size):
            for segment in image.iter_segments():
                start = segment['p_vaddr']
                if segment['p_type'] == 'PT_LOAD' and start <= address and address + size <= start + segment['p_filesz']:
                    offset = address - start
                    return segment.data()[offset:offset + size]
            raise ValueError('Builtin table points outside loaded ELF data')

        def cstring(address):
            data = bytearray()
            for offset in range(128):
                char = read(address + offset, 1)
                if char == b'\0':
                    return data.decode('ascii')
                data.extend(char)
            raise ValueError('Unterminated builtin name')

        count = struct.unpack('<i', read(symbols['g_builtin_count']['st_value'], 4))[0]
        table = symbols['g_builtins']
        if not 1 <= count <= 256 or table['st_size'] != count * 16:
            raise ValueError('Invalid builtin table size')
        found = []
        for i in range(count):
            name, priority, stack, entry = struct.unpack('<IiiI', read(table['st_value'] + i * 16, 16))
            if name and cstring(name) == 'sensor_sample':
                if (entry & ~1) != (symbols['sensor_sample_main']['st_value'] & ~1) or not entry & 1 or stack <= 0:
                    raise ValueError('sensor_sample registration does not point to its ARM entry')
                found.append({'name': 'sensor_sample', 'entry': entry, 'priority': priority, 'stack_bytes': stack})
        if len(found) != 1:
            raise ValueError('Expected exactly one sensor_sample command registration')
    with tempfile.TemporaryDirectory(prefix='sensor_sample-image-check-') as directory:
        rebuilt = Path(directory) / 'kernel.bin'
        subprocess.run(['arm-none-eabi-objcopy', '-O', 'binary', str(elf), str(rebuilt)],
                       check=True, capture_output=True)
        if rebuilt.read_bytes() != binary.read_bytes():
            raise ValueError('Flash binary differs from the checked ELF')
    data = binary.read_bytes()
    if not task_id or not build_id or task_id.encode() + b'\0' not in data or build_id.encode() + b'\0' not in data:
        raise ValueError('Final flash image lacks the expected client identity')
    return {'status': 'pass', 'scope': 'Final image configuration, application table, entry and ELF/BIN identity; not runtime or hardware validation',
            'registration': found[0], 'elf_sha256': digest(elf), 'binary_sha256': digest(binary),
            'config_sha256': digest(config), 'task_id': task_id, 'build_id': build_id}
