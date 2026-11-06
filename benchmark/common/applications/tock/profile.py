import json
CHANNELS = {
    'temperature': (0x60000, 1, 'centi_degC', 1),
    'humidity': (0x60001, 1, 'centi_percent_RH', 0),
    'pressure': (0x60008, 1, 'hPa', 3),
    'atmospheric_pressure': (0x60008, 1, 'hPa', 3),
    'ambient_light': (0x60002, 1, 'lx', 0),
    'proximity': (0x60005, 1, 'device_count', 0),
    'equivalent_co2': (0x60007, 2, 'ppm', 0),
    'equivalent_tvoc': (0x60007, 3, 'ppb', 0),
    'acceleration_xyz': (0x60004, 1, 'um_per_s2', 2),
    'magnetic_field_xyz': (0x60004, 100, 'nT', 2),
}


def rust_config(profile, path):
    rows = []
    for key in ('device', 'app_id', 'profile_id'):
        rows.append(f'const {key.upper()}: &str = {json.dumps(profile[key])};')
    for key in ('rounds', 'startup_ms', 'interval_ms', 'request_timeout_ms'):
        rows.append(f'const {key.upper()}: u32 = {profile[key]};')
    rows.append('const CHANNELS: &[Channel] = &[')
    for c in profile['channels']:
        rows.append('Channel { name: "%s", driver: %d, command: %d, unit: "%s", kind: %d },' %
                    (c['name'], c['driver'], c['command'], c['unit'], c['kind']))
    rows.append('];')
    path.write_text('\n'.join(rows) + '\n')
