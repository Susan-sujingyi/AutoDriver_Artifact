"""Public metadata callbacks must compile against the method-owned ABI shim."""
from pathlib import Path
import hashlib,subprocess,tempfile,unittest

from method.autodriver.implementation.system.native.nuttx_headers import project,mock_headers


class PublicMetadataTests(unittest.TestCase):
    def originals(self,root,*,metadata=True):
        files={
            'include/nuttx/i2c/i2c_master.h':'''/* Synthetic public interface fixture. */
struct i2c_ops_s { int unused; };
struct i2c_config_s { int unused; };
struct i2c_msg_s { int unused; };
struct i2c_master_s { int unused; };
#define I2C_TRANSFER(d,m,n) 0
int i2c_write(void);
int i2c_read(void);
int i2c_writeread(void);
''',
            'include/nuttx/sensors/sensor.h':'''/* Synthetic public interface fixture. */
typedef void (*sensor_push_event_t)(void);
typedef void (*sensor_notify_event_t)(void);
struct sensor_ops_s { int (*get_info)(struct sensor_lowerhalf_s *, struct sensor_device_info_s *); };
struct sensor_lowerhalf_s { const struct sensor_ops_s *ops; };
int sensor_register(struct sensor_lowerhalf_s *, int);
int sensor_unregister(struct sensor_lowerhalf_s *, int);
uint64_t sensor_get_timestamp(void);
''',
            'include/nuttx/uorb.h':'''/* Synthetic public interface fixture. */
typedef float sensor_data_t;
#define SENSOR_TYPE_CUSTOM 0
#define SENSOR_INFO_NAME_SIZE 19
struct sensor_temp { uint64_t timestamp; sensor_data_t temperature; };
struct sensor_humi { uint64_t timestamp; sensor_data_t humidity; };
'''+('struct sensor_device_info_s { int32_t min_delay; char name[SENSOR_INFO_NAME_SIZE]; };\n' if metadata else '')}
        for name,text in files.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        return files

    def test_full_original_metadata_and_limits_make_valid_optional_callback_compilable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);raw=root/'target';files=self.originals(raw)
            headers,audit=project(raw);headers.update(mock_headers('synthetic'))
            self.assertIn('SENSOR_INFO_NAME_SIZE 19',headers['nuttx/uorb.h'])
            for row in audit:
                data=files[row['path']].encode()
                self.assertEqual(row['source_sha256'],hashlib.sha256(data).hexdigest())
                self.assertEqual(row['excerpt_sha256'],hashlib.sha256(data[row['start_byte']:row['end_byte']]).hexdigest())
            include=root/'include'
            for name,text in headers.items():
                path=include/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
            source=root/'callback.c';source.write_text('''#include <nuttx/sensors/sensor.h>
_Static_assert(sizeof(((struct sensor_device_info_s *)0)->name) == 19, "original metadata bound");
int callback(struct sensor_lowerhalf_s *lower, struct sensor_device_info_s *info) {
 (void)lower; info->min_delay = -1; info->name[SENSOR_INFO_NAME_SIZE-1] = 0;
 return SSIZE_MAX > 0 ? 0 : -1;
}
''')
            result=subprocess.run(['/usr/bin/gcc','-std=gnu11','-D_DEFAULT_SOURCE','-Werror','-fsyntax-only',
                '-I',str(include),str(source)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_missing_original_metadata_is_reported_without_inventing_a_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.originals(root,metadata=False)
            with self.assertRaisesRegex(ValueError,'sensor_device_info_s'):project(root)


if __name__=='__main__':unittest.main()
