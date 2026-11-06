"""Host-only controls. They audit evaluator discrimination, not method quality."""

def replace(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def legal_controls(source):
    split = source.replace('polls: Cell<u32>,', 'polls: Cell<u32>, offset: Cell<usize>, bytes: Cell<[u8;6]>,')
    split = split.replace('polls: Cell::new(0),', 'polls: Cell::new(0), offset: Cell::new(0), bytes: Cell::new([0;6]),')
    split = replace(split, 'b[0] = 0x03; self.state.set(State::Data); let _ = self.submit(b, 1, 6);',
                    'self.offset.set(0); b[0] = 0x03; self.state.set(State::Data); let _ = self.submit(b, 1, 1);')
    split = replace(split, 'State::Data => {', '''State::Data => {
                let i = self.offset.get(); let mut bytes = self.bytes.get();
                bytes[i] = b[0]; self.bytes.set(bytes);
                if i < 5 { self.offset.set(i+1); b[0]=3+(i+1) as u8; let _=self.submit(b,1,1); return; }
                b[..6].copy_from_slice(&bytes);''')
    return {
        'split_register_reads': split,
        'reordered_configuration': replace(source, '[(0x00, 0x10), (0x01, 0x20), (0x02, 0x00)]',
                                           '[(0x01, 0x20), (0x00, 0x10), (0x02, 0x00)]'),
        'nearest_rounding': replace(source, '[x * 100_000 / 1100, y * 100_000 / 1100, z * 100_000 / 980]',
                                   '[(x * 100_000 + x.signum() * 550) / 1100, (y * 100_000 + y.signum() * 550) / 1100, (z * 100_000 + z.signum() * 490) / 980]'),
        'reduced_fraction': replace(source, '[x * 100_000 / 1100, y * 100_000 / 1100, z * 100_000 / 980]',
                                   '[x * 1000 / 11, y * 1000 / 11, z * 5000 / 49]'),
        'signed_extension': source.replace('as i32 as u32 as usize', 'as i32 as isize as usize'),
        'default_rate_and_range': source.replace('const SETUP: [(u8, u8); 3] = [(0x00, 0x10), (0x01, 0x20), (0x02, 0x00)];',
                                                'const SETUP: [(u8, u8); 1] = [(0x02, 0x00)];'),
    }


def fault_controls(source):
    def numerical(old, new): return (replace(source, old, new), 'fail')
    return {
        'duplicate_callback': (replace(source, 'client.callback(values[0] as i32 as u32 as usize,',
            'client.callback(values[0] as i32 as u32 as usize, values[1] as i32 as u32 as usize, values[2] as i32 as u32 as usize);\n                    client.callback(values[0] as i32 as u32 as usize,'), 'fail'),
        'wrong_axis_order': numerical('y * 100_000 / 1100, z * 100_000 / 980', 'z * 100_000 / 1100, y * 100_000 / 980'),
        'wrong_z_scale': numerical('z * 100_000 / 980', 'z * 100_000 / 1100'),
        'wrong_units': (source.replace('100_000', '10_000'), 'fail'),
        'wrong_endian': (source.replace('i16::from_be_bytes', 'i16::from_le_bytes'), 'fail'),
        'unsigned_values': (source.replace('i16::from_be_bytes', 'u16::from_be_bytes'), 'fail'),
        'wrong_range': numerical('(0x01, 0x20)', '(0x01, 0x40)'),
        'wrong_rate': numerical('(0x00, 0x10)', '(0x00, 0x14)'),
        'fabricated_callback': numerical('[x * 100_000 / 1100, y * 100_000 / 1100, z * 100_000 / 980]', '[0i64, 0, 0]'),
        'sleep_mode': (replace(source, '(0x02, 0x00)', '(0x02, 0x03)'), 'not_completed'),
    }
