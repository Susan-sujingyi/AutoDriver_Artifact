"""Independent checks against the pinned ELF, TBF v2 and nRF52840 layout.

Pure parsing; never opens a probe. This module is included in download bundles.
"""
from __future__ import annotations
import io
import struct
import tarfile
from pathlib import Path
from elftools.elf.elffile import ELFFile

FLASH_END = 0x100000
APP_FLASH = 0x40000
RAM_START, RAM_END = 0x20000000, 0x20040000
APP_RAM = 0x20010000
STACK = 8192


def require(condition, message):
    if not condition:
        raise ValueError(message)


def elf_info(path):
    data = Path(path).read_bytes()
    elf = ELFFile(io.BytesIO(data))
    require(elf.elfclass == 32 and elf.little_endian and elf['e_machine'] == 'EM_ARM', 'Expected ARM ELF32 little endian')
    require(elf['e_type'] == 'ET_EXEC', 'Expected statically linked executable')
    symtab = elf.get_section_by_name('.symtab')
    require(symtab is not None, 'ELF symbols missing')
    symbols = {s.name: s['st_value'] for s in symtab.iter_symbols() if s.name}
    sections = {s.name: {'address': s['sh_addr'], 'size': s['sh_size'], 'type': s['sh_type'], 'flags': s['sh_flags']}
                for s in elf.iter_sections()}
    segments = [{k: s[k] for k in ('p_vaddr', 'p_paddr', 'p_filesz', 'p_memsz', 'p_flags')}
                for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
    for s in elf.iter_sections():
        require(not (s['sh_flags'] & 2 and s['sh_type'] in ('SHT_REL', 'SHT_RELA') and s['sh_size']), 'Unresolved allocated relocations')
    return {'entry': elf['e_entry'], 'symbols': symbols, 'sections': sections, 'segments': segments}


def load_segments(path):
    elf = ELFFile(io.BytesIO(Path(path).read_bytes()))
    return [(s['p_paddr'], s.data()) for s in elf.iter_segments()
            if s['p_type'] == 'PT_LOAD' and s['p_filesz']]


def kernel_binary(path):
    segments = load_segments(path)
    # The pinned linker emits a four-byte erased-flash .apps sentinel at the
    # application origin. It is not kernel code; the new application replaces
    # it. Reject any other kernel payload in this region.
    app_segments = [(a, b) for a, b in segments if a >= APP_FLASH]
    require(app_segments == [(APP_FLASH, b'\xff' * 4)], 'Unexpected kernel payload in application region')
    segments = [(a, b) for a, b in segments if a < APP_FLASH]
    require(segments and min(a for a, _ in segments) == 0, 'Kernel is not based at address 0')
    end = max(a + len(b) for a, b in segments)
    require(end <= APP_FLASH, 'Kernel overlaps application flash')
    out = bytearray(b'\xff' * end)
    used = bytearray(end)
    for a, b in segments:
        require(not any(used[a:a+len(b)]), 'Overlapping kernel load segments')
        out[a:a+len(b)] = b
        used[a:a+len(b)] = b'\x01' * len(b)
    return bytes(out)


def parse_tbf(data):
    require(len(data) >= 16, 'Truncated TBF base')
    version, header, total, flags, _ = struct.unpack_from('<HHIII', data)
    require(version == 2 and header >= 16 and header % 4 == 0, 'Invalid TBF v2 header')
    require(total == len(data) and header <= total <= FLASH_END-APP_FLASH, 'TBF length/capacity mismatch')
    require(flags == 1, 'App must be enabled and not sticky')
    check = 0
    for (word,) in struct.iter_unpack('<I', data[:header]): check ^= word
    require(check == 0, 'TBF header checksum mismatch')
    tlvs, cursor = {}, 16
    while cursor < header:
        require(cursor + 4 <= header, 'Truncated TBF TLV')
        kind, size = struct.unpack_from('<HH', data, cursor)
        require(kind not in tlvs and size > 0 and cursor + 4 + size <= header, 'Duplicate/invalid TBF TLV')
        tlvs[kind] = data[cursor+4:cursor+4+size]
        cursor += (4 + size + 3) & ~3
    require(cursor == header and all(k in tlvs for k in (1, 3, 5, 8, 9)), 'Required TBF fields missing')
    require(set(tlvs) == {1, 3, 5, 8, 9}, 'Unreviewed TBF extension')
    require(len(tlvs[1]) == 12 and len(tlvs[9]) == 20 and len(tlvs[5]) == 8 and len(tlvs[8]) == 4, 'Wrong TBF field size')
    main = struct.unpack('<III', tlvs[1])
    program = struct.unpack('<IIIII', tlvs[9])
    require(main == program[:3], 'Conflicting Main/Program TBF headers')
    entry, trailer, ram = main
    fixed_ram, fixed_flash = struct.unpack('<II', tlvs[5])
    kernel = struct.unpack('<HH', tlvs[8])
    protected = header + trailer
    require(kernel == (2, 1), 'Expected explicit Tock 2.1 requirement')
    require(fixed_ram == APP_RAM and fixed_flash == APP_FLASH + protected, 'Fixed addresses mismatch')
    require(header <= protected <= entry + header < program[3] <= total, 'Entry/protected/binary extent mismatch')
    require(ram >= STACK and ram <= RAM_END - APP_RAM, 'Invalid minimum app RAM')
    return {'header_size': header, 'total_size': total, 'protected_size': protected,
            'minimum_ram': ram, 'entry': APP_FLASH + header + entry,
            'fixed_ram': fixed_ram, 'fixed_flash': fixed_flash, 'binary_end': program[3],
            'package': tlvs[3].decode('utf-8'), 'kernel_version': list(kernel)}


def validate_images(kernel_elf, kernel_bin, app_elf, tbf, tab):
    k, a = elf_info(kernel_elf), elf_info(app_elf)
    tdata = Path(tbf).read_bytes(); t = parse_tbf(tdata)
    require(Path(kernel_bin).read_bytes() == kernel_binary(kernel_elf), 'Kernel BIN differs from ELF load segments')
    ks = k['symbols']
    sp, reset = struct.unpack_from('<II', Path(kernel_bin).read_bytes())
    require(RAM_START < sp <= ks['_sappmem'] and reset & 1 and (reset & ~1) < APP_FLASH,
            'Kernel vector table stack/reset address invalid')
    require(ks['_sapps'] == APP_FLASH and ks['_eapps'] == FLASH_END, 'Kernel application flash layout mismatch')
    require(RAM_START <= ks['_sappmem'] <= APP_RAM < ks['_eappmem'] == RAM_END, 'Kernel/application RAM overlap')
    for seg in k['segments']:
        if seg['p_vaddr'] >= RAM_START and seg['p_memsz']:
            require(seg['p_vaddr'] + seg['p_memsz'] <= ks['_sappmem'], 'Kernel RAM crosses app memory')
    stack = a['sections']['.stack']
    require(stack['address'] == APP_RAM and stack['size'] == STACK, 'App stack address/size mismatch')
    require(a['symbols']['tbf_protected_region_size'] == t['protected_size'], 'Linker/TBF protected size mismatch')
    require(a['entry'] == t['entry'], 'ELF/TBF entry mismatch')
    require(a['entry'] & 1, 'ARM Thumb entry missing')
    require(a['symbols']['_sram_origin'] == APP_RAM, 'ELF SRAM origin mismatch')
    ram_end = max(s['address'] + s['size'] for s in a['sections'].values()
                  if s['flags'] & 2 and s['address'] >= RAM_START)
    require(ram_end <= APP_RAM + t['minimum_ram'] <= 0x20030000, 'App linker/TBF RAM capacity mismatch')
    # Allow one aligned 64 KiB MPU region including kernel process/grant memory.
    # Runtime grant exhaustion is observable but cannot be proved absent by ELF.
    require(t['minimum_ram'] < 0xE000 and APP_RAM % 0x10000 == 0 and APP_RAM + 0x10000 <= RAM_END,
            'App RAM lacks bounded MPU/process overhead headroom')
    for addr, data in load_segments(app_elf):
        require(APP_FLASH + t['protected_size'] <= addr and addr + len(data) <= APP_FLASH + t['binary_end'], 'ELF segment outside TBF binary')
        require(tdata[addr-APP_FLASH:addr-APP_FLASH+len(data)] == data, 'TBF bytes differ from app ELF')
    with tarfile.open(tab) as archive:
        members = archive.getmembers()
        require(all(m.isfile() and '/' not in m.name and '\\' not in m.name for m in members), 'Unsafe TAB member')
        require(len({m.name for m in members}) == len(members), 'Duplicate TAB member')
        images = [m for m in members if m.name.endswith('.tbf')]
        require(len(images) == 1 and images[0].name == 'cortex-m4.tbf', 'Expected one Cortex-M4 TAB binary')
        require(archive.extractfile(images[0]).read() == tdata, 'TAB/TBF mismatch')
    return {'status': 'pass', 'tbf': t, 'kernel_flash_end': len(Path(kernel_bin).read_bytes()),
            'kernel_app_ram_start': ks['_sappmem'], 'app_ram_static_end': ram_end,
            'stack_bytes': STACK, 'mpu_headroom_policy_bytes': 0x10000,
            'kernel_entry': k['entry'], 'app_entry': a['entry'],
            'runtime_process_loading': 'not_run', 'runtime_grant_allocation': 'not_run'}
