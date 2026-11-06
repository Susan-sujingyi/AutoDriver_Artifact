"""Finite path inputs with SSA, register domains and explicit state lifetime.

This generates stimuli only. Every selected path is replayed by the contract
interpreter; these are never reported as native candidate observations.
"""
import ast
from collections import Counter
from fractions import Fraction

from ..contract.model import Unknown,ContractError,unwrap
from ..contract.data import check_layout,integer,frame_environment
from ..contract.fsm import Interpreter,InputEvent
from ..contract.expression import seconds
from ..contract.timing import Event
from ..contract.domains import symbolic_domain
from ..contract.solver import solve_witness
from ..contract.stimuli import build_frame
from ..contract.wire import transaction


PREFIX='autodriver_witness'


def reference(node):
    if isinstance(node,ast.Name):return node.id
    if isinstance(node,ast.Attribute):
        parent=reference(node.value)
        if parent:return parent+'.'+node.attr
    return None


class PathInputs:
    def __init__(self,contract,graph,edges):
        self.contract,self.graph,self.edges=contract,graph,edges
        self.variables=dict(graph.variables);self.temporary=set();self.frames={}
        self.domains={};self.definitions={};self.types={};self.widths={};self.conditions=[];self.reads={};self.serial=0

    def fresh(self):
        self.serial+=1
        if self.serial>384:raise Unknown('prefix symbolic value bound exceeded')
        return PREFIX+'.v'+str(self.serial)

    def expression(self,value):
        if type(value) is bool:return str(value)
        if type(value) in {int,Fraction}:
            value=Fraction(value);return str(value.numerator) if value.denominator==1 else '('+str(value.numerator)+'/'+str(value.denominator)+')'
        return value

    def environment(self):
        result=dict(self.variables);counts=Counter(key for fields in self.frames.values() for key in fields)
        for frame,fields in self.frames.items():
            for name,symbol in fields.items():
                path=frame+'.'+name
                if path in result:raise Unknown('frame field conflicts with explicit prefix variable')
                result[path]=symbol
                if counts[name]==1 and name not in result:result[name]=symbol
        return result

    def rewrite(self,expression,environment=None):
        if type(expression) is bool:return str(expression)
        if not isinstance(expression,str) or len(expression)>16384:raise Unknown('bounded pure prefix expression required')
        try:tree=ast.parse(expression,mode='eval')
        except (SyntaxError,RecursionError):raise Unknown('unencoded prefix expression') from None
        if sum(1 for _ in ast.walk(tree))>2048:raise Unknown('prefix expression node bound exceeded')
        if any(isinstance(n,ast.Name) and n.id==PREFIX for n in ast.walk(tree)):
            raise Unknown('input expression uses the reserved witness namespace')
        environment=self.environment() if environment is None else environment;owner=self
        class Substitute(ast.NodeTransformer):
            def visit_Name(self,node):return self.replace(node)
            def visit_Attribute(self,node):return self.replace(node)
            def visit_Call(self,node):
                node.args=[self.visit(arg) for arg in node.args]
                for keyword in node.keywords:keyword.value=self.visit(keyword.value)
                return node
            def replace(self,node):
                name=reference(node)
                if name not in environment:return node
                return ast.parse(owner.expression(environment[name]),mode='eval').body
        return ast.unparse(Substitute().visit(tree).body)

    def guard(self,value):
        if isinstance(value,tuple):
            op,*args=value
            if op=='and':return ' and '.join('('+self.guard(x)+')' for x in args) or 'True'
            if op=='not':return 'not ('+self.guard(args[0])+')'
            if op=='remaining':
                counter,limit,deadline=args
                # Deadline feasibility is checked in concrete interpreter
                # replay with zero I/O latency and minimum explicit waits.
                return 'True' if limit is None else '('+self.expression(self.variables[counter])+') < '+str(limit)
            raise Unknown('unencoded derived prefix guard')
        return self.rewrite(value)

    def input(self,width,signed=False):
        width=integer(width,1,4096);symbol=self.fresh()
        self.widths[symbol]=width
        bounds=(-(1<<(width-1)),(1<<(width-1))-1) if signed else (0,(1<<width)-1)
        self.domains[symbol]={'type':'int','bounds':bounds};return symbol

    def begin(self):
        self.frames.clear()
        for name in self.temporary:self.variables.pop(name,None)
        self.temporary.clear()

    def encode(self):
        for index,edge in enumerate(self.edges):
            self.conditions.append(self.guard(edge.guard))
            action=edge.action
            if edge.event==('acquire','logical_success'):self.begin()
            if action:
                kind,*args=action
                if kind=='set':self.variables[args[0]]=args[1]
                elif kind=='increment':
                    value=self.variables[args[0]]
                    if type(value) is not int:raise Unknown('symbolic repeat counter unsupported')
                    self.variables[args[0]]=value+1
                elif kind=='new_acquisition':
                    if edge.event!=('acquire','logical_success'):self.begin()
                elif kind=='io_success':
                    operation=args[0];reads=[]
                    if operation.name=='ReadRegister':
                        if operation['width']%8:raise Unknown('whole-byte register prefix input required')
                        name=operation['into'];symbol=self.input(operation['width'],operation['signed'])
                        if self.graph.variable_types.get(name)=='bool':raise Unknown('register integer conflicts with boolean state')
                        self.variables[name]=symbol
                        if name not in self.graph.variable_types:self.temporary.add(name)
                        reads.append(('register',name,symbol,operation))
                    else:
                        frames=[operation['frame']] if operation.name=='ReadFrame' else [s['into'] for s in operation['segments'] if s.name=='ReadSegment'] if operation.name=='Transfer' else []
                        for name in frames:
                            if name not in self.contract['frames']:raise Unknown('prefix read has no complete frame definition')
                            layout=check_layout(self.contract['frames'][name]);fields={key:self.input(field['width'],field['signed']) for key,field in layout['fields'].items()}
                            self.frames[name]=fields;reads.append(('frame',name,fields,layout))
                    self.reads[index]=reads
                elif kind=='commit':
                    names=args[0]['from_frame'];names=(names,) if isinstance(names,str) else names
                    if not set(names)<=self.frames.keys():raise Unknown('commit depends on old or unavailable prefix frames')
                elif kind not in {'wait'}:raise Unknown('unsupported prefix data action: '+kind)
            snapshot=self.environment();updates={}
            for assignment in edge.effects:
                name=assignment['variable'];symbol=self.fresh()
                self.definitions[symbol]=self.rewrite(assignment['expression'],snapshot)
                if self.definitions[symbol] in self.widths:self.widths[symbol]=self.widths[self.definitions[symbol]]
                self.types[symbol]=self.graph.variable_types[name];updates[name]=symbol
            self.variables.update(updates)
            if edge.target in self.graph.invariants:self.conditions.append(self.rewrite(self.graph.invariants[edge.target]))
        variables,premises=symbolic_domain(self.domains)
        from ..contract.expression import bit_guard_width
        condition=' and '.join('('+c+')' for c in self.conditions) or 'True'
        result=solve_witness(condition,variables,premises,definitions=self.definitions,definition_types=self.types,
            bit_width=bit_guard_width(condition,self.widths))
        if result['status']!='satisfied':raise Unknown('no encoded finite path witness: '+result.get('reason','unknown'))
        return result

    def replay(self,bus,result):
        values={key:int(Fraction(value)) for key,value in result['assignment'].items()}
        machine=Interpreter(self.contract);all_frames={};steps=[]
        for index,edge in enumerate(self.edges):
            if machine.state!=edge.source or not machine.guard(edge.guard):raise Unknown('decoded witness does not enable the selected global path')
            data=None;frame_payloads={};register_payloads={}
            if edge.action and edge.action[0]=='io_success':
                operation=edge.action[1]
                for kind,name,symbols,layout in self.reads[index]:
                    if kind=='frame':
                        checks={c['expected_field'] for c in layout['integrity']}
                        frame_payloads[name]=build_frame(layout,{key:values[symbol] for key,symbol in symbols.items() if key not in checks},name=name)
                        all_frames[name]=frame_payloads[name]
                    else:
                        register_payloads[name]=values[symbols].to_bytes(layout['width']//8,layout['byte_order'],signed=layout['signed'])
                wire=transaction(self.contract,operation,bus,environment=frame_environment(machine.frames,machine.instance,machine.variables,declared_frames=self.contract['frames']))
                data=(next(iter(register_payloads.values())) if operation.name=='ReadRegister' else frame_payloads[operation['frame']] if operation.name=='ReadFrame' else frame_payloads if operation.name=='Transfer' else b'')
                steps.append({'phase':'acquisition' if machine.instance=='acquisition' else 'initialization','operation':operation,'wire':wire,
                              'path':edge.path,'frame_payloads':frame_payloads,'register_payloads':register_payloads})
            if edge.event:
                operation,phase=edge.event
                instance='acquisition' if edge.event==('acquire','logical_success') else machine.instance or 'initialization'
                if machine.instance is None:machine.instance=instance
                if edge.event==('acquire','logical_success') and not (edge.action and edge.action[0]=='new_acquisition'):
                    machine.begin_acquisition(instance)
                if edge.action and edge.action[0]=='wait':
                    wait=edge.action[1];anchor=machine.anchor(wait['of'] if wait.name=='WaitAfterCompletion' else wait['anchor'])
                    machine.now=max(machine.now,anchor.time+seconds(unwrap(wait['minimum'])))
                machine.event=InputEvent(operation,phase,instance,machine.now,data)
                machine.events[(operation,phase,instance)]=Event(operation,phase,instance,machine.now)
            machine.take(edge)
        if machine.sample is None or not any(step['phase']=='acquisition' for step in steps):raise Unknown('no fresh acquisition commit in replayed prefix')
        inputs=frame_environment(machine.sample,machine.instance,machine.sample_variables,declared_frames=self.contract['frames'])
        return {'steps':steps,'frames':all_frames,'bound_input_names':sorted(inputs),'predicate':' and '.join('('+c+')' for c in self.conditions),
            'witness':result['assignment'],'scope':'one interpreter-checked finite data/state prefix, zero I/O latency and minimum declared waits; not candidate observations, provider feasibility, full failure/progress or all-path proof'}
