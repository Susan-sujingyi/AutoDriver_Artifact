"""Exact applicability, partition and arithmetic-domain checks.

Only declared contract ranges are used. This establishes encoded arithmetic
facts, not the truth of recovered evidence or native candidate behavior.
"""
from collections import Counter
from collections.abc import Mapping
from fractions import Fraction
import ast

from .model import Node,ContractError,Unknown,unwrap
from .expression import evaluate,seconds,scalar
from .data import check_layout,frame_status_key
from .solver import prove


def domains_from_frames(contract):
    """Frame bit ranges overapproximate all valid decoded data, including CRC."""
    fields={};counts=Counter()
    for frame,layout in contract['frames'].items():
        for name,field in check_layout(layout)['fields'].items():
            bits=field['width'];lo,hi=(-(1<<(bits-1)),(1<<(bits-1))-1) if field['signed'] else (0,(1<<bits)-1)
            fields[frame+'.'+name]={'type':'int','bounds':(lo,hi)};counts[name]+=1
    for path,domain in list(fields.items()):
        name=path.rsplit('.',1)[1]
        if counts[name]==1 and name!='frame_status':fields[name]=domain
    for frame in contract['frames']:
        key=frame_status_key(frame)
        if key in fields:raise ContractError('frame field conflicts with completeness metadata')
        fields[key]={'type':'bool','bounds':(False,True)}
    return fields


def domains_from_state(contract,frame_domains):
    """Join decoded inputs, initial values and direct state assignments.

    These are mathematical range overapproximations, not C widths, source
    validity or reachability proofs. Unsupported arithmetic writes stay unknown.
    """
    from .fsm import lower
    from .model import walk
    graph=lower(contract['behavior']);inputs=dict(frame_domains);states={};aliases=[];unknown=set()
    for _,value in walk(contract['behavior']):
        if value.name!='ReadRegister' or not isinstance(value['into'],str):continue
        width=value['width']
        if type(width) is not int or not 1<=width<=4096:continue
        bounds=(-(1<<(width-1)),(1<<(width-1))-1) if value['signed'] else (0,(1<<width)-1)
        name=value['into'];previous=inputs.get(name)
        if previous is not None:
            if previous['type']!='int':continue
            bounds=(min(bounds[0],previous['bounds'][0]),max(bounds[1],previous['bounds'][1]))
        inputs[name]={'type':'int','bounds':bounds}
    for name,kind in graph.variable_types.items():
        if name=='frame_status' or name.startswith('frame_status.'):
            raise ContractError('frame_status is reserved for observed frame completeness')
        initial=graph.variables[name]
        states[name]={'type':kind,'bounds':(False,True) if kind=='bool' else (initial,initial)}
        if kind!='bool' and name in inputs:
            lo,hi=inputs[name]['bounds'];states[name]['bounds']=(min(initial,lo),max(initial,hi))
    for edge in graph.edges:
        if edge.action and edge.action[0]=='increment':unknown.add(edge.action[1])
        for assignment in edge.effects:
            name=assignment['variable']
            if graph.variable_types[name]=='bool':continue  # Full Boolean domain, not truth.
            expression=assignment['expression']
            if isinstance(expression,str) and (expression in states or expression in inputs):
                aliases.append((name,expression));continue
            try:
                tree=ast.parse(expression,mode='eval')
                if any(isinstance(n,(ast.Name,ast.Attribute,ast.Call)) for n in ast.walk(tree)):raise Unknown('not constant')
                constant=scalar(expression,{})
                if graph.variable_types[name]=='int' and constant.denominator!=1:raise Unknown('not integer')
                lo,hi=states[name]['bounds'];states[name]['bounds']=(min(lo,constant),max(hi,constant))
            except (Unknown,ContractError,TypeError,SyntaxError,RecursionError):unknown.add(name)
    for name,source in aliases:
        value=states.get(source,inputs.get(source))
        if value is None or value['type'] not in {'int','real'}:unknown.add(name)
    changed=True
    while changed:
        changed=False
        for name,source in aliases:
            if source in unknown and name not in unknown:unknown.add(name);changed=True
    for _ in range(len(states)+1):
        changed=False
        for name,source in aliases:
            if name in unknown or source in unknown:continue
            value=states.get(source,inputs.get(source))
            if value is None or value['type'] not in {'int','real'}:unknown.add(name);continue
            lo,hi=states[name]['bounds'];a,b=value['bounds'];bounds=(min(lo,a),max(hi,b))
            if states[name]['bounds']!=bounds:states[name]['bounds']=bounds;changed=True
        if not changed:break
    else:unknown.update(states)
    inputs.update({name:value for name,value in states.items() if name not in unknown})
    for name in unknown:inputs.pop(name,None)  # Unknown state shadows any short field alias.
    return inputs


def symbolic_domain(domains):
    if not isinstance(domains,Mapping) or len(domains)>128:raise Unknown('explicit bounded domain map required')
    variables={};premises=[]
    for name,declaration in domains.items():
        if not isinstance(declaration,Mapping) or set(declaration)!={'type','bounds'}:
            raise Unknown('domain requires type and closed bounds')
        kind=declaration['type'];bounds=declaration['bounds']
        if kind not in {'int','real','bool'}:raise Unknown('unsupported domain type')
        variables[name]=kind
        if kind=='bool':
            if not isinstance(bounds,(tuple,list)) or tuple(bounds) not in {(False,True),(False,False),(True,True)}:raise ContractError('invalid boolean domain')
            if bounds[0]==bounds[1]:premises.append(name if bounds[0] else 'not '+name)
            continue
        if not isinstance(bounds,(tuple,list)) or len(bounds)!=2 or any(type(x) not in {int,Fraction} for x in bounds):
            raise Unknown('exact numeric bounds required')
        lo,hi=bounds
        if lo>hi or kind=='int' and any(Fraction(x).denominator!=1 for x in bounds):raise ContractError('invalid numeric domain')
        text=lambda v:'('+str(Fraction(v).numerator)+'/'+str(Fraction(v).denominator)+')'
        premises += [name+'>='+text(lo),name+'<='+text(hi)]
    return variables,premises


def partition(cases,domains,*,otherwise=False):
    """Disjointness plus coverage over exactly the declared domain."""
    if not isinstance(cases,Mapping) or not cases or len(cases)>64:raise ContractError('bounded nonempty condition map required')
    variables,premises=symbolic_domain(domains);guards=list(cases)
    if any(not isinstance(g,str) for g in guards):raise ContractError('pure condition strings required')
    disjoint=' and '.join('not (('+a+') and ('+b+'))' for i,a in enumerate(guards) for b in guards[i+1:]) or 'True'
    checks={'disjoint':prove(disjoint,variables,premises)}
    checks['coverage']=prove(' or '.join('('+g+')' for g in guards),variables,premises) if not otherwise else {
        'status':'satisfied','scope':'explicit otherwise covers all unmatched values; branch overlap still checked'}
    statuses={c['status'] for c in checks.values()}
    return {'status':'violated' if 'violated' in statuses else 'unknown' if 'unknown' in statuses else 'satisfied',
            'checks':checks,'scope':'declared input domains; no evidence entailment'}


def select(value,environment):
    """No implicit default for unknown guards or conditionally applicable data."""
    if isinstance(value,Node) and value.name=='Constraint':
        if value['under'] is not None:
            if not evaluate(value['under'],environment):raise Unknown('constraint is inapplicable in this environment')
        return select(value['value'],environment)
    if isinstance(value,Node) and value.name=='ConditionalValue':
        matches=[];unknown=[]
        for guard,item in value['cases'].items():
            try:active=evaluate(guard,environment)
            except Unknown as error:unknown.append(str(error));continue
            if type(active) is not bool:raise ContractError('condition must be boolean')
            if active:matches.append(item)
        if len(matches)>1:raise ContractError('conditional branches overlap in current environment')
        if unknown:raise Unknown('conditional applicability unresolved: '+'; '.join(unknown))
        return select(matches[0] if matches else value['otherwise'],environment)
    return unwrap(value)


def minimum_wait(value,*,environment=None,domains=None):
    """A conservative maximum is allowed only after complete disjoint coverage."""
    try:
        selected=select(value,environment or {})
        return {'status':'satisfied','seconds':seconds(selected),'scope':'explicit current environment'}
    except Unknown:
        if not isinstance(value,Node) or value.name!='ConditionalValue':raise
    otherwise=value['otherwise']
    explicit_default=not isinstance(otherwise,Node) or otherwise.name not in {'Unspecified','Unresolved'}
    checked=partition(value['cases'],domains,otherwise=explicit_default)
    if checked['status']!='satisfied':return dict(checked,seconds=None)
    values=[seconds(select(v,environment or {})) for v in value['cases'].values()]
    if explicit_default:values.append(seconds(select(otherwise,environment or {})))
    return {'status':'satisfied','seconds':max(values),'partition':checked,
            'scope':'all declared conditions; lower bound only, no upper-bound scheduling conclusion'}


def numeric_domain(quantity,domains,*,definitions=None,preconditions=None,definition_widths=None):
    if not isinstance(quantity,Node) or quantity.name!='PhysicalQuantity':raise ContractError('physical quantity required')
    if not isinstance(domains,Mapping):raise Unknown('explicit numeric domain map required')
    if len(domains)>4096:raise Unknown('full numeric domain declaration exceeds inspection bound')
    for name,value in domains.items():symbolic_domain({name:value})
    expression=quantity['expression']
    if not isinstance(expression,str):raise ContractError('pure physical relation required')
    requirements=list(quantity.get('requires',())) if preconditions is None else list(preconditions)
    if preconditions is None and isinstance(quantity.get('domain'),str):requirements.append(quantity['domain'])
    definitions=dict(definitions or {})
    # Select the complete expression/requirement/definition closure. Unrelated
    # decoded fields are still in the immutable contract and checked by their
    # own consumers; they need not consume this proof query's variable bound.
    from .relations import reference
    needed=set();seen=set()
    def inspect(text,depth=0):
        if depth>64:raise Unknown('numeric dependency depth bound exceeded')
        try:tree=ast.parse(text,mode='eval')
        except (SyntaxError,TypeError,RecursionError):raise Unknown('predicate syntax unsupported') from None
        if sum(1 for _ in ast.walk(tree))>2048:raise Unknown('numeric dependency expression node bound exceeded')
        def visit(value,level=0):
            if level>80:raise Unknown('numeric dependency expression depth bound exceeded')
            if isinstance(value,ast.Call):
                for arg in value.args:visit(arg,level+1)
                for keyword in value.keywords:visit(keyword.value,level+1)
                return
            path=reference(value)
            if path:
                if path in definitions:
                    if path not in seen:seen.add(path);inspect(definitions[path],depth+1)
                else:needed.add(path)
                return
            for child in ast.iter_child_nodes(value):visit(child,level+1)
        visit(tree.body)
    inspect(expression)
    for requirement in requirements:inspect(str(requirement) if type(requirement) is bool else requirement)
    variables,premises=symbolic_domain({key:value for key,value in domains.items() if key in needed})
    try:identifiers={node.id for node in ast.walk(ast.parse(expression,mode='eval')) if isinstance(node,ast.Name)}
    except (SyntaxError,RecursionError):raise ContractError('numeric relation syntax invalid') from None
    name='autodriver_numeric_root'
    while name in variables or name in definitions or name in identifiers:name+='_x'
    definitions[name]=expression
    widths=dict(definition_widths or {});widths[name]=quantity.get('bit_width')
    # Named arithmetic definitions enforce a numeric result; comparing a
    # Boolean expression to itself must not certify a PhysicalQuantity.
    result=prove(name+' == '+name,variables,[*premises,*requirements],definitions=definitions,definition_widths=widths)
    return dict(result,conditional_preconditions=requirements,
        scope='definedness under declared bounds and explicit validity requirements; their source truth and candidate guards are not proved')
