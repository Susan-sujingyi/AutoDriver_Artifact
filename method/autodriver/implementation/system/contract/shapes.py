"""Structural framework types, independent of OS/device answers.

These checks validate the meaning of declared constructor slots, not evidence
entailment or provider implementation. Unknown fields remain unknown.
"""
from collections.abc import Mapping
from fractions import Fraction

from .model import Node,ContractError,Unknown,unwrap,walk


def check_framework_shapes(root,report):
    def shape(path,value,constructors,*,optional=False,mapping=False):
        def check():
            item=unwrap(value)
            if optional and item is None:return
            if mapping and isinstance(item,Mapping):return
            if isinstance(item,Node) and item.name in constructors:return
            raise ContractError('expected '+('None or ' if optional else '')+
                                ('a capability map or ' if mapping else '')+' / '.join(sorted(constructors)))
        report.attempt(path,'framework_slot_type',check)

    for key,names in {'i2c':{'APIContract','AsyncI2CContract'},'spi':{'APIContract'},'gpio':{'APIContract'},
                      'alarm':{'APIContract','TickAlarmContract'},'execution':{'ExecutionModel'},'lifecycle':{'LifecycleContract'}}.items():
        shape(key,root[key],names,optional=True,mapping=key in {'i2c','spi','gpio'})
        if key in {'spi','gpio'}:
            for path,item in walk(root[key],key):
                if item.name=='AsyncI2CContract':report.add(path,'violated','bus_specific_contract_type','AsyncI2CContract describes I2C only')
    for key in ('outputs','capabilities','resources'):
        try:item=unwrap(root[key])
        except Unknown as error:
            report.add(key,'unknown','framework_container_type',str(error));continue
        if not isinstance(item,Mapping):
            report.add(key,'violated','framework_container_type','named framework entries require a map')
        elif key=='outputs':
            for name,value in item.items():shape('outputs.'+str(name),value,{'SensorInterface'})
    for path,value in walk(root):
        if value.name=='APIContract':
            def api():
                if not isinstance(value['symbol'],str) or not value['symbol'].strip():raise ContractError('API symbol must be nonempty text')
                if not isinstance(unwrap(value['parameters']),Mapping):raise ContractError('API parameters require a name/type map')
                if any(not isinstance(name,str) or not name for name in value['parameters']):raise ContractError('API parameter names must be nonempty text')
            report.attempt(path,'api_declaration_type',api)
        elif value.name=='Callback':
            report.attempt(path,'callback_arguments_type',lambda: _mapping(unwrap(value['arguments'])))
        elif value.name=='SensorInterface' and value['encoding'] is not None:
            shape(path+'.encoding',value['encoding'],{'OutputEncoding'})
        elif value.name=='OutputEncoding':
            def numbers():
                if not isinstance(value['storage_type'],str) or not value['storage_type']:raise ContractError('native storage type must be nonempty text')
                for key in ('scale','offset'):
                    item=unwrap(value[key])
                    if type(item) not in {int,Fraction}:raise ContractError('encoding '+key+' must be an exact dimensionless number')
                if value['scale']==0:raise ContractError('output scale must be nonzero')
            report.attempt(path,'encoding_numeric_type',numbers)


def _mapping(value):
    if not isinstance(value,Mapping):raise ContractError('callback arguments require a name/type map')
