"""Staged portable ABI projection; definitions copied mechanically from public C."""
from pathlib import Path
import hashlib
from tree_sitter import Language,Parser
import tree_sitter_c


def walk(node):
    yield node
    for child in node.named_children:yield from walk(child)


def declared(node):
    current=node.child_by_field_name('declarator')
    while current is not None:
        if current.type in {'identifier','field_identifier','type_identifier'}:return current
        child=current.child_by_field_name('declarator')
        if child is None and current.type=='parenthesized_declarator' and len(current.named_children)==1:child=current.named_children[0]
        current=child
    return node.child_by_field_name('name')


def project(root,*,sensor_structures=('sensor_temp','sensor_humi')):
    root=Path(root);parser=Parser(Language(tree_sitter_c.language()));audit=[]
    def select(path,names,prefixes=()):
        data=(root/path).read_bytes();masked=data.replace(b'CODE ',b'     ').replace(b'FAR ',b'    ')
        tree=parser.parse(masked);parts=[];seen=set();found=set()
        for child in tree.root_node.named_children:
            if child.type!='comment':break
            parts.append(data[child.start_byte:child.end_byte].decode())
        for node in walk(tree.root_node):
            if node.type not in {'struct_specifier','type_definition','declaration','function_definition','preproc_def','preproc_function_def'}:continue
            name=declared(node)
            if name is None:continue
            text=masked[name.start_byte:name.end_byte].decode()
            if text not in names and not text.startswith(prefixes):continue
            if node.type=='struct_specifier' and node.child_by_field_name('body') is None:continue
            found.add(text);selected=node
            if node.type=='type_definition':
                parent=node.parent
                while parent is not None and parent.type in {'preproc_else','preproc_if','preproc_ifdef'} and parent.end_byte-parent.start_byte<4096:
                    selected=parent;parent=parent.parent
            key=selected.start_byte,selected.end_byte
            if key in seen:continue
            seen.add(key);part=data[key[0]:key[1]].decode()
            if node.type=='struct_specifier':part+=';'
            parts.append(part)
            audit.append({'path':path,'symbol':text,'start_byte':key[0],'end_byte':key[1],
                'source_sha256':hashlib.sha256(data).hexdigest(),'excerpt_sha256':hashlib.sha256(data[key[0]:key[1]]).hexdigest()})
        missing=names-found
        if missing:raise ValueError('public ABI declarations missing: '+path+' '+str(sorted(missing)))
        return '\n'.join(parts)+'\n'
    common='#pragma once\n#include <stdint.h>\n#include <stdbool.h>\n#include <stddef.h>\n#include <sys/types.h>\n#include <limits.h>\n#include <time.h>\n#include <nuttx/config.h>\n#ifndef FAR\n#define FAR\n#define CODE\n#endif\n'
    i2c=select('include/nuttx/i2c/i2c_master.h',{'i2c_ops_s','i2c_config_s','i2c_msg_s','i2c_master_s','I2C_TRANSFER','i2c_write','i2c_read','i2c_writeread'},('I2C_M_',))
    sensors=select('include/nuttx/sensors/sensor.h',{'sensor_ops_s','sensor_lowerhalf_s','sensor_push_event_t','sensor_notify_event_t','sensor_register','sensor_unregister','sensor_get_timestamp'})
    if any(not isinstance(name,str) or not name.startswith('sensor_') or not name.replace('_','').isalnum() for name in sensor_structures):
        raise ValueError('invalid public sensor structure name')
    # The public get_info operation writes this complete framework metadata
    # object. A forward declaration suffices for the vtable signature, but
    # does not let a valid lower half implement its callback. Copy the original
    # definition and its named-size constants; never supply device values.
    uorb=select('include/nuttx/uorb.h',{'sensor_data_t','sensor_device_info_s',*sensor_structures},('SENSOR_TYPE_','SENSOR_INFO_'))
    forwards='struct file;\nstruct sensor_info_s;\nstruct sensor_device_info_s;\nstruct sensor_lowerhalf_s;\nstruct i2c_master_s;\nstruct i2c_msg_s;\n'
    return {'nuttx/i2c/i2c_master.h':common+forwards+i2c,'nuttx/uorb.h':common+uorb,
        'nuttx/sensors/sensor.h':common+'#include <nuttx/uorb.h>\n#include <nuttx/clock.h>\n'+forwards+sensors},audit


def mock_headers(module):
    """OS service mocks; the public sensor and I2C ABI above stays extracted."""
    return {
      'nuttx/config.h':'#pragma once\n#define FAR\n#define CODE\n#define OK 0\n#define ERROR -1\n#define UNUSED(x) ((void)(x))\n#define CONFIG_I2C 1\n#define CONFIG_SENSORS 1\n#define CONFIG_SENSORS_USE_FLOAT 1\n#define CONFIG_SENSORS_'+module.upper()+' 1\n#define CONFIG_USEC_PER_TICK 1000\n',
      'nuttx/compiler.h':'#pragma once\n#include <nuttx/config.h>\n#define inline_function inline\n#define weak_function __attribute__((weak))\n',
      'nuttx/clock.h':'#pragma once\n#include <stdint.h>\n#include <time.h>\n#define USEC_PER_SEC 1000000\n#define NSEC_PER_USEC 1000\n#define USEC_PER_TICK 1000\n#define MSEC_PER_TICK 1\n#define TICK2USEC(t) ((t)*1000)\n#define USEC2TICK(t) (((t)+999)/1000)\n#define MSEC2TICK(t) (t)\n#define TICK2MSEC(t) (t)\nclock_t clock_systime_ticks(void);\nvoid clock_systime_timespec(struct timespec *ts);\n',
      'nuttx/signal.h':'#pragma once\n#include <sys/types.h>\n#include <unistd.h>\nint nxsig_usleep(useconds_t delay);\n',
      'nuttx/kmalloc.h':'#pragma once\n#include <stddef.h>\nvoid *kmm_malloc(size_t size);\nvoid *kmm_zalloc(size_t size);\nvoid *kmm_calloc(size_t n,size_t size);\nvoid kmm_free(void *p);\n',
      'nuttx/mutex.h':'#pragma once\n#include <stdbool.h>\ntypedef struct {bool locked;} mutex_t;\n#define NXMUTEX_INITIALIZER {false}\nint nxmutex_init(mutex_t *m);\nint nxmutex_destroy(mutex_t *m);\nint nxmutex_lock(mutex_t *m);\nint nxmutex_unlock(mutex_t *m);\nint nxmutex_trylock(mutex_t *m);\n',
      'nuttx/arch.h':'#pragma once\nvoid up_udelay(unsigned int us);\nvoid up_mdelay(unsigned int ms);\n',
      'debug.h':'#pragma once\n#define _info(...) ((void)0)\n#define _err(...) ((void)0)\n#define sninfo(...) ((void)0)\n#define snerr(...) ((void)0)\n',
      'nuttx/debug.h':'#pragma once\n#include <debug.h>\n'}
