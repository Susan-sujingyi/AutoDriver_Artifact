"""Integer rounding boundary inputs from explicit BP/FP/DBC relations only."""
from fractions import Fraction
import re

from ..contract.model import walk,Unknown,ContractError
from ..contract.domains import domains_from_frames,symbolic_domain
from ..contract.data import check_layout,decode_frame,frame_environment
from ..contract.expression import evaluate,quantity
from ..contract.solver import solve_witness
from ..contract.stimuli import build_frame
from .plan import parent_path


def integer_ties(contract,fp,bp,frames):
    cases=[];gaps=[]
    for path,binding in walk(bp):
        if binding.name not in {'NumericEncoding','DeliverRequestedQuantity'}:continue
        if getattr(binding['physical_relation'],'name',None)!='FromDBC' or getattr(binding['encoding'],'name',None)!='FromFP':continue
        try:
            relation=parent_path(contract,binding['physical_relation']['path'],'dbc');encoding=parent_path(fp,binding['encoding']['path'],'fp')
            rounding=binding['rounding']
            if rounding.name!='Rounding' or rounding['mode']!='nearest_ties_even':continue
            match=re.fullmatch(r'([iu])(8|16|32|64|128)',encoding['storage_type'])
            if not match:continue
            bits=int(match[2]);lo,hi=(-(1<<(bits-1)),(1<<(bits-1))-1) if match[1]=='i' else (0,(1<<bits)-1)
            source=quantity(1,relation['unit']);native=quantity(1,encoding['unit'])
            if source.dimensions!=native.dimensions:raise ContractError('numeric stimulus unit mismatch')
            literal=lambda x:'('+str(Fraction(x).numerator)+'/'+str(Fraction(x).denominator)+')'
            domains=domains_from_frames({'frames':{name:contract['frames'][name] for name in sorted(frames)}})
            variables,premises=symbolic_domain(domains)
            from ..contract.relations import Relations,finite_value
            model=Relations(contract['quantities'],external_names=set(domains))
            relation_path=binding['physical_relation']['path'].removeprefix('dbc.')
            relation_name=relation_path[len('quantities.'):] if relation_path.startswith('quantities.') else None
            definitions=model.solver_definitions(relation_name) if relation_name is not None else {}
            widths=model.solver_widths(relation_name) if relation_name is not None else {}
            root_name='autodriver_numeric_stimulus'
            while root_name in variables or root_name in definitions:root_name+='_x'
            definitions[root_name]=relation['expression'];widths[root_name]=relation.get('bit_width')
            expression='(('+root_name+'*'+literal(source.value/native.value)+'-'+literal(encoding['offset'])+')/'+literal(encoding['scale'])+')'
            for name in domains:
                if '.' not in name:
                    full=[key for key in domains if key.endswith('.'+name)]
                    if len(full)==1:premises.append(name+' == '+full[0])
            domain=relation['domain']
            if isinstance(domain,str):premises.append(domain)
            premises.extend(model.solver_preconditions(relation_name) if relation_name is not None else relation.get('requires',()))
            premises.extend([expression+'>='+str(lo),expression+'<='+str(hi)])
            for name,remainder in [('even','1/2'),('odd','3/2')]:
                predicate='('+expression+') % 2 == '+remainder
                result=solve_witness(predicate,variables,premises,definitions=definitions,definition_widths=widths,timeout_ms=1000)
                if result['status']!='satisfied':
                    if result['status']=='unknown':gaps.append({'binding':path,'reason':result.get('reason','numeric witness unknown')})
                    continue
                witness=result['assignment'];payloads={};decoded={}
                for frame in sorted(frames):
                    layout=check_layout(contract['frames'][frame]);checks={c['expected_field'] for c in layout['integrity']}
                    values={field:int(Fraction(witness[frame+'.'+field])) for field in layout['fields'] if field not in checks}
                    data=build_frame(layout,values,name=frame);payloads[frame]=data
                    decoded[frame]=decode_frame(layout,data,name=frame,instance='witness')
                environment=frame_environment(decoded,'witness',{},declared_frames=contract['frames'])
                environment[root_name]=model.value(relation_name,environment) if relation_name is not None else finite_value(relation,environment)
                if evaluate(predicate,environment) is not True:raise Unknown('computed integrity values do not satisfy rounding witness')
                cases.append({'case':'rounding_tie_'+name+':'+str(len(cases)),'frames':payloads,'predicate':predicate,'expected_valid':True})
        except (Unknown,ContractError,KeyError,ValueError,TypeError) as error:gaps.append({'binding':path,'reason':str(error)})
    return {'cases':cases,'gaps':gaps,'scope':'finite integer tie inputs from explicit physical relation, encoding and rounding; no invented precision tolerance'}
