"""Compile public ABI probes with synthetic protocols, without a model or scorer."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from method.autodriver.implementation.system.contract.model import parse,node,Unknown
from method.autodriver.implementation.system.native.nuttx import native_plan,evaluate
from method.autodriver.implementation.system.native.nuttx_channels import SCALARS


ROOT=Path(__file__).resolve().parents[2]


class NativeNuttXPlanTests(unittest.TestCase):
    def execute_probe(self,*,wrong_command=False,ignore_interruption=False):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);root=Path(folder.name)
        target=root/'target';(target/'include/nuttx/sensors').mkdir(parents=True)
        for name in ('include/nuttx/uorb.h','include/nuttx/sensors/sensor.h','include/nuttx/i2c/i2c_master.h'):
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/'third_party/nuttx'/name,path)
        (target/'include/nuttx/sensors/synthetic.h').write_text('#pragma once\nstruct i2c_master_s;\nint synthetic_register(struct i2c_master_s*,int,int);\n')
        task={'target':{'os':'nuttx','module':'synthetic','interface':{'framework':'new_sensor'}},
            'requirements':{'functions':{'channels':list(SCALARS)},'connection':{'i2c':{'address_7bit':17}}}}
        dbc=parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Transfer([WriteSegment([48]),ReadSegment(1,into='cal')],name='setup',boundary='stop'),Sequence(Transfer([WriteSegment([49]),ReadSegment(1,into='a')],name='first',boundary='stop'),WaitAfterCompletion('first','2 ms'),Transfer([WriteSegment([50]),ReadSegment(2,into='b')],name='second',boundary='stop'),CommitValidatedSample(['a','b'])),on_failure=FailCurrentOperation(True),recovery=None,reentry=None),frames={'cal':FrameLayout(1,{'offset':Field(0,8,'big')}),'a':FrameLayout(1,{'x':Field(0,8,'big')}),'b':FrameLayout(2,{'y':Field(0,16,'big')})})",'dbc')
        plan=native_plan(task,dbc,root)
        include=root/'include'
        for name,text in plan['headers'].items():
            p=include/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        main=root/'main.c';main.write_text(plan['source'])
        driver=root/'driver.c'
        descriptors=','.join('{.lower={.type='+kind+',.ops=&ops},.size=sizeof(struct '+structure+'),.offset=offsetof(struct '+structure+','+field+'),.stamp=offsetof(struct '+structure+',timestamp)}' for structure,field,kind in SCALARS.values())
        driver.write_text('''#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/i2c/i2c_master.h>
#include <nuttx/signal.h>
struct device {struct sensor_lowerhalf_s lower;bool enabled;size_t size,offset,stamp;};
static struct i2c_master_s *bus;
static int exchange(unsigned char command,unsigned char *data,int length){
 struct i2c_msg_s messages[2]={{.addr=17,.frequency=100000,.buffer=&command,.length=1},{.addr=17,.frequency=100000,.flags=I2C_M_READ,.buffer=data,.length=length}};
 return I2C_TRANSFER(bus,messages,2);
}
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enabled){(void)filep;((struct device*)lower)->enabled=enabled;return 0;}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t size){
 (void)filep;struct device *dev=(struct device*)lower;if(!dev->enabled)return -ENODEV;if(!buffer||size<dev->size)return -EINVAL;
 unsigned char a[1],b[2];int result=exchange(@COMMAND@,a,1);if(result<0)return result;result=nxsig_usleep(2000);@INTERRUPTION@result=exchange(50,b,2);if(result<0)return result;
 memset(buffer,0,dev->size);float value=a[0];uint64_t stamp=sensor_get_timestamp();memcpy(buffer+dev->offset,&value,sizeof(value));memcpy(buffer+dev->stamp,&stamp,sizeof(stamp));return dev->size;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
static struct device devices[]={@DEVICES@};
int synthetic_register(struct i2c_master_s *controller,int address,int devno){
 (void)address;bus=controller;unsigned char cal[1];int result=exchange(48,cal,1);if(result<0)return result;
 for(size_t i=0;i<sizeof(devices)/sizeof(*devices);i++){result=sensor_register(&devices[i].lower,devno);if(result<0){while(i)sensor_unregister(&devices[--i].lower,devno);return result;}}return 0;
}
'''.replace('@DEVICES@',descriptors).replace('@COMMAND@','51' if wrong_command else '49').replace('@INTERRUPTION@','' if ignore_interruption else 'if(result<0)return result;'))
        binary=root/'probe';build=subprocess.run(['/usr/bin/gcc','-std=gnu11','-D_DEFAULT_SOURCE','-I',str(include),str(main),str(driver),'-lm','-o',str(binary)],capture_output=True,text=True,timeout=30)
        self.assertEqual(build.returncode,0,build.stderr)
        chunks=[]
        for i in range(len(plan['cases'])):
            run=subprocess.run([str(binary),str(i)],capture_output=True,text=True,timeout=5);self.assertEqual(run.returncode,0,run.stderr);chunks.append(run.stdout)
        report=evaluate(dbc,node('FrameworkProfile',outputs={}),node('BindingPlan',obligations=()),plan,''.join(chunks))
        return plan,report

    def test_multi_frame_and_combined_native_transactions_on_all_scalar_abis(self):
        plan,report=self.execute_probe()
        self.assertEqual(set(plan['native_channels']),set(SCALARS))
        self.assertEqual({case['frame'] for case in plan['cases']},{'a','b'})
        self.assertTrue(all(not case['violations'] for case in report['cases']),[(c['case'],c['violations']) for c in report['cases'] if c['violations']])
        self.assertTrue(all(case['contract_projection']['checked_io']>=1 for case in report['cases']))
        self.assertEqual(report['status'],'unknown')

    def test_candidate_wire_regression_is_detected_by_same_contract_projection(self):
        _,report=self.execute_probe(wrong_command=True)
        self.assertEqual(report['status'],'violated')
        self.assertTrue(any('wire output differs' in finding for case in report['cases'] for finding in case['violations']))

    def test_actual_interrupted_sleep_cannot_satisfy_the_complete_device_wait(self):
        _,report=self.execute_probe(ignore_interruption=True)
        cases=[case for case in report['cases'] if case['case'].startswith('interrupted_sleep:')]
        self.assertEqual(len(cases),len(SCALARS))
        self.assertTrue(all(any('minimum wait violated' in finding for finding in case['violations']) for case in cases))

    def test_unsupported_vector_is_not_interpreted_as_an_arbitrary_scalar(self):
        from method.autodriver.implementation.system.native.nuttx_channels import scalar_channels
        with self.assertRaisesRegex(Unknown,'does not implement channel'):
            scalar_channels({'requirements':{'functions':{'channels':['magnetic_field_xyz']}}})
