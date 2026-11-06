"""Bounded frame stimuli derived only from current typed contract layouts.

These are checker/executor inputs, not native candidate observations. No device
category, command table, independent oracle or benchmark fixture is consulted.
"""
from .model import Node,ContractError,Unknown,identity
from .data import check_layout,integer,crc8,decode_frame,DataInvalid
from .expression import scalar


def field_bytes(payload,field,value):
    width,offset=field['width'],field['offset']
    lo,hi=(-(1<<(width-1)),(1<<(width-1))-1) if field['signed'] else (0,(1<<width)-1)
    value=integer(value,lo,hi)
    if field['byte_order']=='little':
        payload[offset//8:(offset+width)//8]=value.to_bytes(width//8,'little',signed=field['signed'])
    else:
        if value<0:value+=1<<width
        shift=len(payload)*8-offset-width;mask=((1<<width)-1)<<shift
        raw=(int.from_bytes(payload,'big')&~mask)|(value<<shift)
        payload[:]=raw.to_bytes(len(payload),'big')


def build_frame(layout,values,*,name='generated',instance='stimulus'):
    layout=check_layout(layout)
    if len(layout['fields'])>128:raise Unknown('frame stimulus field bound exceeded')
    checks=list(layout['integrity']);expected={check['expected_field'] for check in checks}
    required=set(layout['fields'])-expected
    if set(values)!=required:raise ContractError('stimulus needs every non-checksum field exactly once')
    payload=bytearray(layout['length'])
    for field,value in values.items():field_bytes(payload,layout['fields'][field],value)
    remaining=checks.copy();completed=set()
    while remaining:
        progress=False
        for check in remaining.copy():
            covered={i for a,b in check['covered_bytes'] for i in range(a,b)}
            dependencies=set()
            for field in expected:
                item=layout['fields'][field]
                if covered.intersection(range(item['offset']//8,(item['offset']+item['width']+7)//8)):dependencies.add(field)
            if dependencies-completed:continue
            data=bytes(payload[i] for a,b in check['covered_bytes'] for i in range(a,b))
            algorithm=check['algorithm']
            value=crc8(data,algorithm) if isinstance(algorithm,Node) and algorithm.name=='CRC8' else scalar(
                algorithm,{f'byte{i}':byte for i,byte in enumerate(data)},bit_width=8)
            if getattr(value,'denominator',1)!=1:raise ContractError('checksum must encode an integer')
            field_bytes(payload,layout['fields'][check['expected_field']],int(value))
            completed.add(check['expected_field']);remaining.remove(check);progress=True
        if not progress:raise Unknown('cyclic checksum dependencies cannot be synthesized')
    result=bytes(payload)
    decode_frame(layout,result,name=name,instance=instance)
    return result


def frame_cases(contract,*,maximum_cases=64):
    if type(maximum_cases) is not int or not 1<=maximum_cases<=256:raise ContractError('bounded stimulus count required')
    cases=[]
    for name,value in contract['frames'].items():
        layout=check_layout(value);checks=layout['integrity'];expected={check['expected_field'] for check in checks}
        fields={key:field for key,field in layout['fields'].items() if key not in expected}
        values={}
        for label,position in [('minimum',0),('middle',1),('maximum',2)]:
            current={}
            for key,field in fields.items():
                width=field['width'];lo,hi=(-(1<<(width-1)),(1<<(width-1))-1) if field['signed'] else (0,(1<<width)-1)
                current[key]=(lo,(lo+hi)//2,hi)[position]
            payload=build_frame(layout,current,name=name)
            if payload in values:continue
            values[payload]=label
            cases.append({'frame':name,'case':label,'expected_valid':True,'data':list(payload),'fields':current})
        if not values:continue
        baseline=next(iter(values))
        for check in checks:
            field=layout['fields'][check['expected_field']];offset=field['offset'];data=bytearray(baseline)
            data[offset//8]^=1<<(7-offset%8)
            try:decode_frame(layout,bytes(data),name=name,instance='stimulus')
            except DataInvalid:pass
            else:raise Unknown('checksum mutation did not yield a known invalid frame')
            cases.append({'frame':name,'case':'integrity_fault:'+check['expected_field'],'expected_valid':False,'data':list(data)})
    if len(cases)>maximum_cases:raise Unknown('complete configured frame-case set exceeds stimulus bound')
    return {'schema':'autodriver.contract-stimuli/v1','contract_sha256':identity(contract),'cases':cases,
            'scope':'finite layout boundary values and one fault per checksum; not native execution, exhaustive input coverage or an independent oracle'}
