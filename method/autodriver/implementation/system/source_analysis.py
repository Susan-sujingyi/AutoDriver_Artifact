"""Configuration-aware C control/data dependency facts, independent of devices.

This is a bounded may-analysis, not a C proof engine. Pointer aliases, unresolved
macros, indirect calls and unbounded loops are explicit gaps. No source is built
or changed, and no behavior category or sensor answer table is embedded.
"""
from __future__ import annotations

from collections import defaultdict,deque
from dataclasses import dataclass,asdict
from pathlib import Path
from fractions import Fraction
import operator
import shlex
import json

from .evidence import descendants,SourceLocations
from .contract.model import ContractError,Unknown
from .contract.expression import evaluate


def cmake_source_membership(source,base,configuration):
    """Recognized literal Zephyr source-list commands, never CMake execution.

    This is build-membership parsing, separate from the C CFG analysis. Nested
    control/functions and variable-expanded filenames remain unknown.
    """
    lexer=shlex.shlex(source,posix=True,punctuation_chars='()');lexer.whitespace_split=True;lexer.commenters='#'
    tokens=list(lexer);records=[];i=0;nesting=0
    while i<len(tokens):
        command=tokens[i];i+=1
        if i>=len(tokens) or tokens[i]!='(':continue
        i+=1;depth=1;arguments=[]
        while i<len(tokens) and depth:
            token=tokens[i];i+=1
            if token=='(':depth+=1
            elif token==')':depth-=1
            if depth:arguments.append(token)
        command=command.casefold()
        if command in {'endif','endforeach','endwhile','endfunction','endmacro'}:nesting=max(0,nesting-1)
        if command in {'zephyr_library_sources','zephyr_library_sources_ifdef','zephyr_library_sources_ifndef'}:
            condition=None;active=True if command=='zephyr_library_sources' else None
            if command!='zephyr_library_sources' and arguments:
                condition=arguments.pop(0)
                if condition in configuration:
                    active=bool(configuration[condition])
                    if command.endswith('_ifndef'):active=not active
            if nesting:active=None
            for argument in arguments:
                if any(c in argument for c in '${};()'):continue
                path=str(Path(base)/argument)
                records.append({'path':path,'command':command,'condition':condition,'active':active,
                                'scope':'literal source-list membership; nested build control not evaluated'})
        if command in {'if','foreach','while','function','macro'}:nesting+=1
    return records


def preprocessor_value(node,source,configuration,depth=0):
    if node is None or depth>64:return None
    text=lambda n:source[n.start_byte:n.end_byte].decode() if n is not None else ''
    child=lambda n:preprocessor_value(n,source,configuration,depth+1)
    kind=node.type
    if kind=='identifier':
        value=configuration.get(text(node))
        return int(value) if type(value) in {bool,int} else None
    if kind=='preproc_defined':
        name=next((text(n) for n in node.named_children if n.type=='identifier'),None)
        return int(configuration[name] is not False) if name in configuration else None
    if kind=='number_literal':
        try:value=int(text(node),0)
        except ValueError:return None
        return value if -(1<<63)<=value<(1<<63) else None
    if kind=='parenthesized_expression':return child(node.named_children[0]) if node.named_children else None
    if kind=='unary_expression':
        value=child(node.child_by_field_name('argument'));op=text(node.child_by_field_name('operator'))
        if value is None:return None
        function={'!':lambda x:int(not x),'-':operator.neg,'+':operator.pos,'~':operator.invert}.get(op)
        return function(value) if function else None
    if kind=='binary_expression':
        a=child(node.child_by_field_name('left'));b=child(node.child_by_field_name('right'));op=text(node.child_by_field_name('operator'))
        if op=='&&' and (a==0 or b==0):return 0
        if op=='||' and (a is not None and a!=0 or b is not None and b!=0):return 1
        if a is None or b is None:return None
        if op in {'/','%'}:
            if b==0:return None
            quotient=int(Fraction(a,b));return quotient if op=='/' else a-quotient*b
        if op in {'<<','>>'} and (not 0<=b<64 or a<0):return None
        function={'+':operator.add,'-':operator.sub,'*':operator.mul,'&':operator.and_,'|':operator.or_,'^':operator.xor,
                  '<<':operator.lshift,'>>':operator.rshift,'==':operator.eq,'!=':operator.ne,'<':operator.lt,'<=':operator.le,
                  '>':operator.gt,'>=':operator.ge,'&&':lambda x,y:bool(x and y),'||':lambda x,y:bool(x or y)}.get(op)
        if function:
            value=int(function(a,b));return value if -(1<<63)<=value<(1<<63) else None
    return None


@dataclass
class Fact:
    identity: str
    kind: str
    text: str
    start_line: int
    end_line: int
    reads: list
    writes: list
    may_writes: list
    calls: list
    control: list
    configuration: list


class CAnalysis:
    def __init__(self,evidence,configuration=None,*,maximum_nodes=20000):
        self.evidence=evidence;self.configuration=dict(configuration or {})
        self.maximum_nodes=maximum_nodes;self.functions={};self.facts={};self.edges=[];self.gaps=[];self.excluded=[]

    def add(self,material_id):
        entry=self.evidence.registry[material_id]
        if entry['kind']!='device_source' or Path(entry['path']).suffix not in {'.c','.h'}:
            raise ContractError('C source analysis requires this task registered C input')
        source=self.evidence.read(material_id)
        from tree_sitter import Parser,Language
        import tree_sitter_c
        language=Language(tree_sitter_c.language());parser=Parser(language);tree=parser.parse(source)
        locations=SourceLocations(source)
        text=lambda node:source[node.start_byte:node.end_byte].decode('utf8') if node is not None else ''
        if tree.root_node.has_error:self.gaps.append({'material_id':material_id,'reason':'syntax/macros incomplete; retained may-facts are not a full effective AST'})

        def lvalue(node):
            if node is None:return None
            if node.type in {'identifier','field_identifier'}:return text(node)
            if node.type=='field_expression':return text(node).replace(' ','')
            if node.type=='subscript_expression':
                argument=node.child_by_field_name('argument')
                return (lvalue(argument) or text(argument))+'[*]'
            if node.type in {'pointer_expression','unary_expression'}:
                return '*'+text(node.child_by_field_name('argument'))
            if node.type in {'parenthesized_expression','init_declarator','pointer_declarator','array_declarator'}:
                child=node.child_by_field_name('declarator')
                if child is None and node.named_children:child=node.named_children[0]
                return lvalue(child)
            return None

        def reads(node):
            if node is None:return set()
            if node.type in {'identifier','field_expression','subscript_expression','pointer_expression'}:
                value=lvalue(node)
                result={value} if value else set()
                if node.type=='subscript_expression':result |= reads(node.child_by_field_name('index'))
                return result
            if node.type=='call_expression':
                args=node.child_by_field_name('arguments')
                return set().union(*(reads(child) for child in args.named_children)) if args else set()
            if node.type=='assignment_expression':
                result=reads(node.child_by_field_name('right'))
                left=node.child_by_field_name('left');operator=node.child_by_field_name('operator')
                if text(operator)!='=':result |= reads(left)
                # Index expressions are evaluated even for a plain store.
                if left is not None and left.type=='subscript_expression':result |= reads(left.child_by_field_name('index'))
                return result
            if node.type=='init_declarator':return reads(node.child_by_field_name('value'))
            if node.type in {'type_identifier','primitive_type','sized_type_specifier','field_identifier'}:return set()
            return set().union(*(reads(child) for child in node.named_children)) if node.named_children else set()

        def writes(node):
            result=set()
            for item in descendants(node):
                target=None
                if item.type=='assignment_expression':target=item.child_by_field_name('left')
                elif item.type=='init_declarator':target=item.child_by_field_name('declarator')
                elif item.type=='update_expression':target=item.child_by_field_name('argument')
                if target is not None:
                    name=lvalue(target)
                    if name:result.add(name)
            return result

        def precondition(node):
            condition=node.child_by_field_name('condition') or node.child_by_field_name('name')
            value=text(condition)
            if node.type=='preproc_ifdef':
                if value not in self.configuration:return value,None
                defined=self.configuration[value] is not False
                negative=source[node.start_byte:node.start_byte+7]==b'#ifndef'
                return ('not defined ' if negative else 'defined ')+value,not defined if negative else defined
            if condition is None:return value,None
            result=preprocessor_value(condition,source,self.configuration)
            return value,None if result is None else result!=0

        def enclosing_configuration(node):
            result=[];child=node;parent=node.parent
            while parent is not None:
                if parent.type in {'preproc_if','preproc_ifdef','preproc_elif'}:
                    predicate,active=precondition(parent)
                    is_alternative=child.type in {'preproc_else','preproc_elif'}
                    result.append({'condition':predicate,'branch':'otherwise' if is_alternative else 'then',
                                   'active':not active if is_alternative and active is not None else active,
                                   'line':locations.line(parent.start_byte)})
                child=parent;parent=parent.parent
            return list(reversed(result))

        for function in descendants(tree.root_node):
            if function.type!='function_definition':continue
            declaration=function.child_by_field_name('declarator');name_node=declaration
            while name_node is not None and name_node.type!='identifier':name_node=name_node.child_by_field_name('declarator')
            if name_node is None:continue
            name=text(name_node);fid=f'{material_id}:{locations.line(function.start_byte)}:{name}'
            selected=enclosing_configuration(function)
            if any(condition['active'] is False for condition in selected):
                self.excluded.append({'function':fid,'material_id':material_id,'sha256':entry['sha256'],
                                      'configuration':selected,'reason':'function excluded by explicit frozen preprocessor selection'})
                continue
            function_facts=[];cfg_edges=[];serial=0
            memory_names=set()
            for declaration_node in descendants(function):
                if declaration_node.type in {'pointer_declarator','array_declarator'}:
                    declared=declaration_node.child_by_field_name('declarator')
                    while declared is not None and declared.type!='identifier':declared=declared.child_by_field_name('declarator')
                    if declared is not None:memory_names.add(text(declared))

            def new(node,kind,controls):
                nonlocal serial
                if len(self.facts)>=self.maximum_nodes:raise Unknown('source dependency node bound reached')
                serial+=1;identifier=f'{fid}:{serial}'
                calls=[]
                may_writes=set()
                structural=kind in {'entry','exit','branch_true','branch_false','loop_entry','loop_exit'} or kind=='loop_guard' and node.type.endswith('statement')
                for item in (() if structural else descendants(node)):
                    if item.type=='call_expression':
                        called=item.child_by_field_name('function');arguments=item.child_by_field_name('arguments')
                        callee=text(called)
                        calls.append({'callee':callee,'arguments':[text(arg) for arg in arguments.named_children] if arguments else [],
                                      'argument_reads':[sorted(reads(arg)) for arg in arguments.named_children] if arguments else [],
                                      'line':locations.line(item.start_byte),'direct':called is not None and called.type=='identifier'})
                        for argument in arguments.named_children if arguments else ():
                            if argument.type=='pointer_expression' and text(argument.child_by_field_name('operator'))=='&':
                                target=lvalue(argument.child_by_field_name('argument'))
                                if target:may_writes.add(target)
                            elif argument.type=='identifier' and text(argument) in memory_names:
                                may_writes.add(text(argument)+'[*]')
                configuration=enclosing_configuration(node)
                fact=Fact(identifier,kind,'' if structural else text(node),locations.line(node.start_byte),locations.line(max(node.start_byte,node.end_byte-1)),
                          [] if structural else sorted(reads(node)),[] if structural else sorted(writes(node)),sorted(may_writes),calls,list(controls),configuration)
                self.facts[identifier]=fact;function_facts.append(identifier)
                return identifier

            def connect(sources,target,label='next'):
                cfg_edges.extend({'from':source_id,'to':target,'kind':'control_flow','branch':label} for source_id in sources)

            def block(node,incoming,controls=(),break_to=None,continue_to=None):
                if node is None:return incoming
                if node.type in {'preproc_if','preproc_ifdef','preproc_elif'}:
                    alternatives=[child for child in node.named_children if child.type in {'preproc_else','preproc_elif'}]
                    predicate,active=precondition(node)
                    current=incoming
                    if active is not False:
                        for child in node.named_children:
                            if child in alternatives or child==node.child_by_field_name('condition') or child==node.child_by_field_name('name'):continue
                            if any(c['active'] is False for c in enclosing_configuration(child)):continue
                            current=block(child,current,controls,break_to,continue_to)
                    if active is True:return current
                    other=block(alternatives[0],incoming,controls,break_to,continue_to) if alternatives else incoming
                    if active is None:self.gaps.append({'function':fid,'line':locations.line(node.start_byte),'reason':'preprocessor condition not resolved: '+predicate})
                    return other if active is False else current|other
                if node.type in {'compound_statement','preproc_else'}:
                    current=incoming
                    for child in node.named_children:
                        if any(c['active'] is False for c in enclosing_configuration(child)):continue
                        current=block(child,current,controls,break_to,continue_to)
                    return current
                if node.type=='if_statement':
                    condition=node.child_by_field_name('condition');branch=new(condition,'guard',controls);connect(incoming,branch)
                    yes_entry=new(condition,'branch_true',controls);no_entry=new(condition,'branch_false',controls)
                    connect({branch},yes_entry,'true');connect({branch},no_entry,'false')
                    yes=block(node.child_by_field_name('consequence'),{yes_entry},(*controls,{'guard':branch,'value':True}),break_to,continue_to)
                    no=block(node.child_by_field_name('alternative'),{no_entry},(*controls,{'guard':branch,'value':False}),break_to,continue_to)
                    return yes|no
                if node.type=='else_clause':
                    children=node.named_children
                    return block(children[-1],incoming,controls,break_to,continue_to) if children else incoming
                if node.type in {'for_statement','while_statement','do_statement'}:
                    initial=node.child_by_field_name('initializer');current=block(initial,incoming,controls,break_to,continue_to) if initial else incoming
                    condition=node.child_by_field_name('condition')
                    header=new(condition or node,'loop_guard',controls)
                    exit_id=new(node,'loop_exit',controls)
                    body=node.child_by_field_name('body')
                    if node.type=='do_statement':
                        body_entry=new(body,'loop_entry',controls);connect(current,body_entry)
                        end=block(body,{body_entry},controls,exit_id,header);connect(end,header)
                        connect({header},body_entry,'true')
                    else:
                        connect(current,header)
                        end=block(body,{header},(*controls,{'guard':header,'value':True}),exit_id,header)
                    update=node.child_by_field_name('update');end=block(update,end,controls,exit_id,header) if update else end
                    if node.type!='do_statement':connect(end,header,'repeat')
                    if condition is not None:connect({header},exit_id,'false')
                    self.gaps.append({'function':fid,'line':locations.line(node.start_byte),'reason':'loop may-analysis; iteration count/progress not proven'})
                    return {exit_id}
                fact=new(node,'return' if node.type=='return_statement' else 'statement',controls);connect(incoming,fact)
                if node.type=='return_statement':
                    connect({fact},function_exit,'return');return set()
                if node.type=='break_statement':
                    if break_to:connect({fact},break_to,'break')
                    else:self.gaps.append({'function':fid,'line':locations.line(node.start_byte),'reason':'break target not resolved'})
                    return set()
                if node.type=='continue_statement':
                    if continue_to:connect({fact},continue_to,'continue')
                    return set()
                if node.type in {'switch_statement','goto_statement','labeled_statement'}:
                    self.gaps.append({'function':fid,'line':locations.line(node.start_byte),'reason':'switch/goto control not lowered; dependency facts are conservative'})
                return {fact}

            entry_id=new(name_node,'entry',());function_exit=new(name_node,'exit',())
            ends=block(function.child_by_field_name('body'),{entry_id});connect(ends,function_exit)
            self.functions[fid]={'name':name,'material_id':material_id,'path':entry['path'],'sha256':entry['sha256'],
                'start_line':locations.line(function.start_byte),'end_line':locations.line(max(function.start_byte,function.end_byte-1)),
                'facts':function_facts,'configuration':enclosing_configuration(function),
                'parameters':[text(p) for p in descendants(declaration) if p.type=='parameter_declaration']}
            self.edges.extend(cfg_edges)
            self._reaching_definitions(function_facts,cfg_edges)
            self._control_dependencies(function_facts,cfg_edges,function_exit)
        return self

    def _reaching_definitions(self,ids,edges):
        predecessors=defaultdict(set)
        for edge in edges:predecessors[edge['to']].add(edge['from'])
        outgoing={i:{} for i in ids};changed=True;iterations=0
        while changed and iterations<max(1,len(ids)*2):
            changed=False;iterations+=1
            for i in ids:
                incoming=defaultdict(set)
                for p in predecessors[i]:
                    for variable,definitions in outgoing[p].items():incoming[variable].update(definitions)
                updated={v:set(ds) for v,ds in incoming.items()}
                for variable in self.facts[i].writes:updated[variable]={i}
                for variable in self.facts[i].may_writes:updated.setdefault(variable,set()).add(i)
                if updated!=outgoing[i]:outgoing[i]=updated;changed=True
        if changed:self.gaps.append({'reason':'reaching-definition iteration bound; dependencies may be incomplete'})
        seen=set()
        for i in ids:
            fact=self.facts[i]
            for variable in fact.reads:
                definitions=set().union(*(outgoing[p].get(variable,set()) for p in predecessors[i])) if predecessors[i] else set()
                for definition in definitions:
                    edge=(definition,i,variable)
                    if edge not in seen:self.edges.append({'from':definition,'to':i,'kind':'data_dependency','variable':variable});seen.add(edge)
            if any('*' in v or '->' in v or '[*]' in v for v in fact.reads+fact.writes):
                self.gaps.append({'fact':i,'reason':'field/array/pointer access is lexical; aliasing and indirect memory effects require analysis'})
            if fact.may_writes:self.gaps.append({'fact':i,'reason':'call may mutate passed memory; actual direction/completion needs source API effects'})

    def _control_dependencies(self,ids,edges,exit_id):
        successors=defaultdict(set)
        for e in edges:successors[e['from']].add(e['to'])
        post={i:({i} if i==exit_id else set(ids)) for i in ids}
        for _ in range(len(ids)*2):
            changed=False
            for i in ids:
                if i==exit_id:continue
                new={i}|set.intersection(*(post[s] for s in successors[i])) if successors[i] else {i}
                if new!=post[i]:post[i]=new;changed=True
            if not changed:break
        else:self.gaps.append({'reason':'postdominator iteration bound; control dependencies incomplete'})
        for edge in edges:
            if edge['branch'] not in {'true','false'}:continue
            guard=edge['from'];branch={'guard':guard,'value':edge['branch']=='true'}
            for dependent in post[edge['to']]-post[guard]:
                if branch not in self.facts[dependent].control:self.facts[dependent].control.append(branch)

    def report(self):
        by_name=defaultdict(list)
        for identity,function in self.functions.items():by_name[function['name']].append(identity)
        calls=[]
        for identity,fact in self.facts.items():
            for call in fact.calls:
                targets=by_name.get(call['callee'],[]) if call['direct'] else []
                calls.append({'caller_fact':identity,**call,'targets':targets,
                              'resolution':'unique_syntax_target' if len(targets)==1 else 'ambiguous' if targets else 'external_or_indirect'})
        interprocedural=[]
        for call in calls:
            for target in call['targets']:
                function=self.functions[target]
                interprocedural.append({'kind':'argument_binding','from':call['caller_fact'],'to':function['facts'][0],
                    'arguments':call['arguments'],'formal_declarations':function['parameters'],
                    'scope':'syntactic argument positions; pointer aliases not established'})
                for fact in function['facts']:
                    if self.facts[fact].kind=='return':interprocedural.append({'kind':'return_dependency','from':fact,'to':call['caller_fact']})
        return {'schema':'autodriver.source-dependencies/v1','functions':self.functions,
                'facts':{i:asdict(f) for i,f in self.facts.items()},
                'dependencies':sorted(self.edges+interprocedural,key=lambda e:json.dumps(e,sort_keys=True)),
                'calls':calls,'gaps':self.gaps,
                'configuration':self.configuration,'excluded':self.excluded,
                'scope':'bounded syntax/control-flow/reaching-definition may-analysis; not full macro/type/alias analysis or device semantic proof'}

    def slice(self,roots,*,maximum_facts=1024):
        report=self.report();selected_functions={i for i,f in self.functions.items() if f['name'] in roots or i in roots}
        pending=deque(selected_functions)
        by_fact={fact:i for i,f in self.functions.items() for fact in f['facts']}
        while pending:
            function=pending.popleft()
            for call in report['calls']:
                if by_fact.get(call['caller_fact'])==function:
                    for target in call['targets']:
                        if target not in selected_functions:selected_functions.add(target);pending.append(target)
        ids={fact for function in selected_functions for fact in self.functions[function]['facts']}
        if len(ids)>maximum_facts:raise Unknown('semantic function closure exceeds fact budget; choose a smaller complete question')
        return {**report,'roots':list(roots),'functions':{i:f for i,f in self.functions.items() if i in selected_functions},
                'facts':{i:f for i,f in report['facts'].items() if i in ids},
                'dependencies':[e for e in report['dependencies'] if e['from'] in ids and e['to'] in ids],
                'calls':[c for c in report['calls'] if c['caller_fact'] in ids]}
