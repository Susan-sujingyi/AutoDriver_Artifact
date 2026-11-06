"""Mechanical wire projection from typed operations, with no device table.

ReadSegment specifies received bytes, so outgoing SPI clock bytes in that
segment are unconstrained. Required output/dummy values need WriteSegment.
Physical provider/CS/bit-order assurance is a separate obligation.
"""
from dataclasses import dataclass
from collections.abc import Mapping
import ast

from .model import ContractError,Unknown,unwrap
from .data import byte_sequence,check_layout,integer
from .expression import scalar


@dataclass(frozen=True)
class Wire:
    bus: str
    write: tuple
    read_length: int
    slices: tuple
    result_kind: str

    def check(self,submitted):
        write=byte_sequence(submitted['write']);read=integer(submitted['read_len'],0,65536)
        if len(write)!=len(self.write) or any(expected is not None and observed!=expected for expected,observed in zip(self.write,write)):
            raise ContractError('native wire output differs from the enabled typed device transaction')
        legal={self.read_length}
        if self.bus=='spi' and self.read_length==0:legal.add(len(self.write))
        if read not in legal:raise ContractError('native wire input length differs from the enabled typed device transaction')

    def project(self,data):
        data=byte_sequence(data)
        if self.read_length:
            if len(data)!=self.read_length:raise ContractError('short or oversized native read completion')
        elif data and (self.bus!='spi' or len(data)!=len(self.write)):
            raise ContractError('unexpected native completion payload')
        if self.result_kind=='empty':return b''
        values={name:data[start:start+length] for name,start,length in self.slices}
        if self.result_kind=='segments':return values
        return next(iter(values.values()))


def register_encoding(operation,read):
    encoding=unwrap(operation['encoding'])
    required={'address_bits','address_byte_order','read_header','write_header','dummy_read_bytes','auto_increment'}
    if not isinstance(encoding,Mapping) or set(encoding)!=required:raise Unknown('complete explicit register wire encoding required')
    bits=integer(encoding['address_bits'],1,64);address=integer(operation['address'],0,(1<<bits)-1)
    if encoding['address_byte_order'] not in {'big','little'}:raise ContractError('invalid register address byte order')
    header=encoding['read_header' if read else 'write_header']
    if not isinstance(header,(tuple,list)) or len(header)>64:raise ContractError('bounded explicit register header required')
    values=[]
    for expression in header:
        if isinstance(expression,str):
            if len(expression)>16384:raise Unknown('register header expression bound exceeded')
            try:tree=ast.parse(expression,mode='eval')
            except (SyntaxError,RecursionError):raise ContractError('register header expression syntax invalid') from None
            if sum(1 for _ in ast.walk(tree))>2048:raise Unknown('register header expression node bound exceeded')
            functions={'min','max','abs','sqrt','exp','ln','extract','signed','unsigned'}
            names={n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}
            if names-{'address'}-functions:
                raise ContractError('register headers may reference only address; write value bytes are appended by WriteRegister.value, not included in write_header')
        value=scalar(expression,{'address':address},bit_width=max(bits,8)) if isinstance(expression,str) else expression
        if getattr(value,'denominator',1)!=1:raise ContractError('register header expression must yield an exact byte')
        if type(value) is not bool and getattr(value,'denominator',None)==1:value=int(value)
        values.append(integer(value,0,255))
    return values,integer(encoding['dummy_read_bytes'],0,64)


def transaction(contract,operation,bus,*,environment=None):
    if bus not in {'i2c','spi'}:raise Unknown('wire adapter supports typed I2C/SPI transactions')
    name=operation.name;write=[];reads=[];result='empty'
    if name=='WriteBytes':write=list(byte_sequence(operation['data']))
    elif name=='ReadFrame':
        length=check_layout(contract['frames'][operation['frame']])['length']
        reads=[(operation['frame'],0,length)];result='bytes'
        if bus=='spi':write=[None]*length
    elif name=='Transfer':
        if len(operation['segments'])>128:raise Unknown('native wire segment bound exceeded')
        seen_read=False;names=set();read_offset=0
        for segment in operation['segments']:
            if segment.name=='WriteSegment':
                if bus=='i2c' and seen_read:raise Unknown('public I2C adapter cannot preserve writes after a read in one transaction')
                data=byte_sequence(segment['data'])
                if len(write)+len(data)>65536:raise Unknown('wire transaction byte bound exceeded')
                write.extend(data)
            elif segment.name=='ReadSegment':
                length=integer(segment['length'],1,65536);frame=segment['into']
                if not isinstance(frame,str) or frame in names:raise Unknown('unique named complete read segments required')
                if read_offset+length>65536 or bus=='spi' and len(write)+length>65536:raise Unknown('wire transaction byte bound exceeded')
                names.add(frame);offset=len(write) if bus=='spi' else read_offset
                reads.append((frame,offset,length));read_offset+=length;seen_read=True
                if bus=='spi':write.extend([None]*length)
            else:raise Unknown('unsupported typed transfer segment')
        result='segments' if reads else 'empty'
    elif name in {'ReadRegister','WriteRegister'}:
        reading=name=='ReadRegister';write,dummy=register_encoding(operation,reading)
        width=integer(operation['width'],1,4096)
        if not reading:
            value=operation['value']
            if isinstance(value,str):
                value=scalar(value,environment or {},bit_width=width)
                if value.denominator!=1:raise ContractError('register value expression must yield an exact integer')
                value=int(value)
            value=integer(value,-(1<<(width-1)),(1<<width)-1)
        if width%8:raise Unknown('whole-byte register wire width required')
        length=width//8
        if operation['byte_order'] not in {'big','little'}:raise ContractError('invalid register value byte order')
        if reading:
            offset=len(write)+dummy if bus=='spi' else dummy
            reads=[(operation['into'],offset,length)];result='bytes'
            if bus=='spi':write.extend([None]*(dummy+length))
        else:
            write.extend(value.to_bytes(length,operation['byte_order'],signed=value<0))
    else:raise Unknown('wire projection unsupported for '+name)
    read_length=(len(write) if bus=='spi' else max(start+length for _,start,length in reads)) if reads else 0
    if len(write)>65536 or read_length>65536:raise Unknown('wire transaction byte bound exceeded')
    return Wire(bus,tuple(write),read_length,tuple(reads),result)
