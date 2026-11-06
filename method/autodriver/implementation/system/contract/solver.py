"""Small, explicitly encoded exact-arithmetic proof queries with counterexamples."""
import ast
from collections.abc import Mapping
from fractions import Fraction

from .model import ContractError, Unknown


def prove(condition, variables, assumptions=(), *, timeout_ms=2000, definitions=None, definition_types=None,
          definition_widths=None,bit_width=None,existential=False):
    """Prove a pure arithmetic predicate over declared symbolic input types.

    No encoding is attempted for provider behavior, real-time progress, arbitrary
    source code, undeclared bit widths, transcendentals, or undefined variable domains.
    """
    if not 1 <= timeout_ms <= 10000:raise ContractError('invalid solver timeout bound')
    if len(variables)>128 or len(assumptions)>128:
        return {'status':'unknown','reason':'solver query exceeds configured bounds','scope':'no property encoded or proved'}
    import z3
    definitions={} if definitions is None else definitions
    definition_types={} if definition_types is None else definition_types
    definition_widths={} if definition_widths is None else definition_widths
    if not isinstance(definitions,Mapping):
        raise ContractError('bounded named arithmetic definitions required')
    if len(definitions)>256:
        return {'status':'unknown','reason':'named arithmetic definition count exceeds solver bound','scope':'no property encoded or proved'}
    if (not isinstance(definition_types,Mapping) or set(definition_types)-set(definitions)
            or any(kind not in {'bool','int','real'} for kind in definition_types.values())):
        raise ContractError('invalid named expression type declarations')
    if set(definitions)&set(variables):raise Unknown('ambiguous input and named arithmetic definition')
    def width_ok(value):return value is None or type(value) is int and 1<=value<=4096
    if (not isinstance(definition_widths,Mapping) or set(definition_widths)-set(definitions)
            or not width_ok(bit_width) or any(not width_ok(value) for value in definition_widths.values())):
        raise ContractError('invalid explicit bit width declarations')
    for name in definitions:
        if not isinstance(name,str) or any(not p.isidentifier() or p.startswith('_') for p in name.split('.')):
            raise ContractError('invalid named arithmetic definition')
    symbols={};domains=[];definition_values={};definition_pending=set();definition_nodes=0
    for name,kind in variables.items():
        if not isinstance(name,str) or any(not p.isidentifier() or p.startswith('_') for p in name.split('.')):
            raise ContractError('invalid symbolic variable name')
        factory={'bool':z3.Bool,'int':z3.Int,'real':z3.Real}.get(kind)
        if factory is None: raise Unknown('unsupported symbolic type')
        symbols[name]=factory(name)

    def encode(expression,*,predicate=True,word_width=None):
        nonlocal definition_nodes
        if type(expression) is bool: return z3.BoolVal(expression)
        if not isinstance(expression,str) or len(expression)>8192:
            raise Unknown('bounded pure predicate required')
        try: tree=ast.parse(expression,mode='eval')
        except (SyntaxError,RecursionError): raise Unknown('predicate syntax unsupported') from None
        count=sum(1 for _ in ast.walk(tree))
        if count>1024: raise Unknown('predicate node bound exceeded')
        if not predicate:
            definition_nodes+=count
            if definition_nodes>8192:raise Unknown('named arithmetic definition node bound exceeded')

        def path(n):
            if isinstance(n,ast.Name): return n.id
            if isinstance(n,ast.Attribute): return path(n.value)+'.'+n.attr
            raise Unknown('symbolic reference unsupported')

        def number(value):
            if not z3.is_arith(value): raise ContractError('arithmetic predicate type mismatch')
            return value

        def real(value):return z3.ToReal(value) if z3.is_int(value) else value

        def boolean(value):
            if not z3.is_bool(value): raise ContractError('boolean predicate type required')
            return value

        def integer(value):
            value=number(value)
            if z3.is_int(value):return value
            domains.append(z3.IsInt(value));return z3.ToInt(value)

        def word(value):
            if word_width is None:raise Unknown('bit operation requires declared width')
            value=integer(value);domains.extend([value>=-(1<<(word_width-1)),value<(1<<word_width)])
            return value

        def constant(value):
            value=z3.simplify(integer(value))
            if not z3.is_int_value(value):raise Unknown('constant bit width/shift parameter required')
            return value.as_long()

        def run(n,depth=0):
            if depth>64: raise Unknown('symbolic expression depth exceeded')
            child=lambda x:run(x,depth+1)
            if isinstance(n,ast.Constant):
                if type(n.value) is bool: return z3.BoolVal(n.value)
                if type(n.value) is int: return z3.IntVal(n.value)
                if type(n.value) is float:
                    f=Fraction(ast.get_source_segment(expression,n));return z3.RealVal(f'{f.numerator}/{f.denominator}')
            if isinstance(n,(ast.Name,ast.Attribute)):
                name=path(n)
                if name in definitions:
                    if name not in definition_values:
                        if name in definition_pending:raise ContractError('cyclic named arithmetic definition: '+name)
                        if len(definition_pending)>=64:raise Unknown('named arithmetic definition depth exceeded')
                        definition_pending.add(name)
                        kind=definition_types.get(name)
                        value=encode(definitions[name],predicate=kind=='bool',word_width=definition_widths.get(name))
                        if kind=='int' and not z3.is_int(value):domains.append(z3.IsInt(number(value)))
                        definition_values[name]=value
                        definition_pending.remove(name)
                    return definition_values[name]
                if name not in symbols: raise Unknown('unencoded symbolic variable: '+name)
                return symbols[name]
            if isinstance(n,ast.UnaryOp):
                value=child(n.operand)
                if isinstance(n.op,ast.Not):return z3.Not(boolean(value))
                if isinstance(n.op,ast.USub):return -number(value)
                if isinstance(n.op,ast.UAdd):return number(value)
                if isinstance(n.op,ast.Invert):
                    value=word(value);return z3.BV2Int(~z3.Int2BV(value,word_width),is_signed=False)
            if isinstance(n,ast.BoolOp):
                values=[boolean(child(x)) for x in n.values]
                return z3.And(*values) if isinstance(n.op,ast.And) else z3.Or(*values)
            if isinstance(n,ast.BinOp):
                l,r=number(child(n.left)),number(child(n.right))
                if isinstance(n.op,ast.Add):return l+r
                if isinstance(n.op,ast.Sub):return l-r
                if isinstance(n.op,ast.Mult):return l*r
                if isinstance(n.op,(ast.BitAnd,ast.BitOr,ast.BitXor,ast.LShift,ast.RShift)):
                    left=word(l)
                    if isinstance(n.op,(ast.LShift,ast.RShift)):
                        shift=constant(r)
                        if not 0<=shift<word_width:raise ContractError('shift exceeds declared width')
                        result=left*(1<<shift) if isinstance(n.op,ast.LShift) else left/(1<<shift)
                    else:
                        right=word(r)
                        # One extra sign bit preserves the exact mathematical
                        # operation on both admitted signed/unsigned integers.
                        a,b=z3.Int2BV(left,word_width+1),z3.Int2BV(right,word_width+1)
                        bits=a&b if isinstance(n.op,ast.BitAnd) else a|b if isinstance(n.op,ast.BitOr) else a^b
                        result=z3.BV2Int(bits,is_signed=True)
                    return word(result)
                if isinstance(n.op,(ast.Div,ast.FloorDiv,ast.Mod)):
                    domains.append(r!=0)
                    ratio=real(l)/real(r)
                    if isinstance(n.op,ast.Div):return ratio
                    denominator=z3.simplify(r)
                    # Preserve integer sorts for positive constant divisors.
                    # This is the same floor semantics and avoids nested
                    # ToInt/ToReal terms in parametric clock formulas.
                    quotient=l/denominator if z3.is_int(l) and z3.is_int_value(denominator) and denominator.as_long()>0 else z3.ToInt(ratio)
                    if isinstance(n.op,ast.FloorDiv):return quotient
                    return l-r*quotient
                if isinstance(n.op,ast.Pow):
                    exponent=n.right;sign=1
                    if isinstance(exponent,ast.UnaryOp) and isinstance(exponent.op,(ast.USub,ast.UAdd)):
                        sign=-1 if isinstance(exponent.op,ast.USub) else 1;exponent=exponent.operand
                    if not isinstance(exponent,ast.Constant) or type(exponent.value) is not int or abs(exponent.value)>64:
                        raise Unknown('only literal integer powers of magnitude at most 64 are encoded')
                    power=sign*exponent.value
                    # Multiplication/division keeps exact rational semantics;
                    # symbolic real exponentiation has different Z3 semantics.
                    result=z3.IntVal(1) if z3.is_int(l) else z3.RealVal(1);base=l;remaining=abs(power)
                    while remaining:
                        if remaining&1:result=result*base
                        remaining//=2
                        if remaining:base=base*base
                    if power<0:
                        domains.append(l!=0);result=z3.RealVal(1)/real(result)
                    return z3.simplify(result)
            if isinstance(n,ast.Compare):
                values=[child(n.left)]+[child(x) for x in n.comparators];results=[]
                for l,r,op in zip(values,values[1:],n.ops):
                    if z3.is_bool(l) != z3.is_bool(r):raise ContractError('symbolic comparison type mismatch')
                    if isinstance(op,ast.Eq):results.append(l==r)
                    elif isinstance(op,ast.NotEq):results.append(l!=r)
                    else:
                        l,r=number(l),number(r)
                        if isinstance(op,ast.Lt):results.append(l<r)
                        elif isinstance(op,ast.LtE):results.append(l<=r)
                        elif isinstance(op,ast.Gt):results.append(l>r)
                        elif isinstance(op,ast.GtE):results.append(l>=r)
                        else:raise Unknown('comparison operator not encoded')
                return z3.And(*results)
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and not n.keywords:
                args=[number(child(x)) for x in n.args]
                if n.func.id=='abs' and len(args)==1:return z3.If(args[0]>=0,args[0],-args[0])
                if n.func.id in {'min','max'} and args:
                    result=args[0]
                    for arg in args[1:]:result=z3.If(result<=arg if n.func.id=='min' else result>=arg,result,arg)
                    return result
                if n.func.id in {'signed','unsigned','extract'} and len(args)==(3 if n.func.id=='extract' else 2):
                    width=constant(args[-1])
                    if not 1<=width<=4096:raise ContractError('invalid bit-vector width')
                    if n.func.id=='extract':
                        offset=constant(args[1]);value=word(args[0])
                        if not 0<=offset or offset+width>word_width:raise ContractError('extract outside declared width')
                        return (value/(1<<offset))%(1<<width)
                    value=integer(args[0]);domains.extend([value>=0,value<(1<<width)])
                    return value-z3.If(value>=(1<<(width-1)),1<<width,0) if n.func.id=='signed' else value
            raise Unknown('expression form is not encoded by this solver')

        return boolean(run(tree.body)) if predicate else number(run(tree.body))

    try:
        premises=[encode(x,word_width=bit_width) for x in assumptions]
        property_=encode(condition,word_width=bit_width)
    except Unknown as error:
        return {'status':'unknown','reason':str(error),'scope':'encoded predicate only'}
    solver=z3.Solver();solver.set(timeout=timeout_ms);solver.add(*premises)
    if existential:
        solver.add(*domains,property_);answer=solver.check()
        return {'status':'satisfied' if answer==z3.sat else 'violated' if answer==z3.unsat else 'unknown',
            'assignment':{name:str(solver.model().eval(sym,model_completion=True)) for name,sym in symbols.items()} if answer==z3.sat else None,
            'reason':solver.reason_unknown() if answer==z3.unknown else 'existential finite input witness only',
            'scope':'one assignment satisfying all encoded domains and conditions; not a universal property proof',
            'solver_version':z3.get_version_string()}
    base=solver.check()
    if base != z3.sat:
        return {'status':'unknown','reason':'inconsistent assumptions' if base==z3.unsat else solver.reason_unknown()}
    if domains:
        solver.push();solver.add(z3.Not(z3.And(*domains)));defined=solver.check()
        if defined != z3.unsat:
            result={'status':'violated' if defined==z3.sat else 'unknown','reason':'expression may be undefined',
                    'counterexample':{name:str(solver.model().eval(sym,model_completion=True)) for name,sym in symbols.items()} if defined==z3.sat else None}
            solver.pop();return result
        solver.pop()
    solver.add(z3.Not(property_));answer=solver.check()
    return {'status':'satisfied' if answer==z3.unsat else 'violated' if answer==z3.sat else 'unknown',
            'counterexample':{name:str(solver.model().eval(sym,model_completion=True)) for name,sym in symbols.items()} if answer==z3.sat else None,
            'reason':solver.reason_unknown() if answer==z3.unknown else 'arithmetic proof/counterexample',
            'scope':'declared symbolic types and assumptions; no unencoded properties', 'solver_version':z3.get_version_string()}


def solve_witness(condition,variables,assumptions=(),*,definitions=None,definition_types=None,definition_widths=None,bit_width=None,timeout_ms=1000):
    return prove(condition,variables,assumptions,definitions=definitions,definition_types=definition_types,
                 definition_widths=definition_widths,bit_width=bit_width,timeout_ms=timeout_ms,existential=True)
