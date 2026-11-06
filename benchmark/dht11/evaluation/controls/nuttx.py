"""Private unit and checksum defects in native New Sensor events."""
def mutations():
    return {
        'wrong_temperature_unit': ('sample.t.temperature=frame[2];','sample.t.temperature=frame[2]*100;'),
        'checksum_ignored': ('if (checksum != frame[4]) return -EBADMSG;','if (false) return -EBADMSG;'),
    }
