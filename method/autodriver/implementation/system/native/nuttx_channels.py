"""Public New Sensor scalar ABI names; no device transactions or formulas."""
from ..contract.model import Unknown, ContractError


SCALARS = {
    'temperature': ('sensor_temp', 'temperature', 'SENSOR_TYPE_AMBIENT_TEMPERATURE'),
    'humidity': ('sensor_humi', 'humidity', 'SENSOR_TYPE_RELATIVE_HUMIDITY'),
    'pressure': ('sensor_baro', 'pressure', 'SENSOR_TYPE_BAROMETER'),
    'ambient_light': ('sensor_light', 'light', 'SENSOR_TYPE_LIGHT'),
    'proximity': ('sensor_prox', 'proximity', 'SENSOR_TYPE_PROXIMITY'),
    'co2': ('sensor_co2', 'co2', 'SENSOR_TYPE_CO2'),
    'tvoc': ('sensor_tvoc', 'tvoc', 'SENSOR_TYPE_TVOC'),
}
ALIASES = {'atmospheric_pressure': 'pressure', 'equivalent_co2': 'co2', 'equivalent_tvoc': 'tvoc'}


def scalar_channels(task):
    requested = task['requirements']['functions']['channels']
    if not isinstance(requested, list) or not requested:
        raise ContractError('nonempty public channel list required')
    channels = [ALIASES.get(name, name) for name in requested]
    if len(set(channels)) != len(channels):
        raise Unknown('multiple task channels alias one native output')
    for channel in channels:
        if channel not in SCALARS:
            raise Unknown('native NuttX scalar adapter does not implement channel: ' + str(channel))
    return channels


def render(channels):
    """C accesses must compile against the mechanically projected public ABI."""
    descriptors = []
    for channel in channels:
        structure, field, kind = SCALARS[channel]
        descriptors.append('{"' + channel + '","' + structure + '",' + kind
            + ',sizeof(struct ' + structure + '),offsetof(struct ' + structure + ',' + field
            + '),offsetof(struct ' + structure + ',timestamp)}')
    return ','.join(descriptors)
