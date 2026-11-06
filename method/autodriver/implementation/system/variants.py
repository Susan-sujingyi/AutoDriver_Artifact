"""Trusted mechanism interventions. Model responses cannot select variants."""
from dataclasses import dataclass,asdict


@dataclass(frozen=True)
class Variant:
    source_framework_lifting: bool=True
    dbc_evidence_refinement: bool=True
    binding_plan: bool=True
    candidate_contract_repair: bool=True

    def settings(self):return asdict(self)


VARIANTS={
    'full':Variant(),
    'without_source_framework_lifting':Variant(source_framework_lifting=False),
    'without_dbc_evidence_refinement':Variant(dbc_evidence_refinement=False),
    'without_binding_plan':Variant(binding_plan=False),
    'compile_only_repair':Variant(candidate_contract_repair=False),
}


def variant(name):
    if name not in VARIANTS:raise ValueError('Unknown trusted method variant')
    return VARIANTS[name]


def admit_call(name,request,registry,*,dbc_candidate_seen=False):
    """Applied by trusted orchestration before assembly, never from a response."""
    policy=variant(name)
    entries={entry['id']:entry for entry in registry['materials']}
    stage=request['stage'];target=request.get('repair_target')
    view_only=request.get('view_only') is True
    if view_only:
        if stage!='repair' or target not in {'fp','dbc','bp'}:
            raise ValueError('derived view compilation must identify one readonly device/binding object')
        if any(s['role'] not in {'method_specification','candidate','dbc','target_fp','validation_report'} for s in request['selections']):
            raise ValueError('view compilation cannot retrieve/recheck originals or candidate code')
        if target=='fp' and (request.get('framework_role')!='target' or any(
                s['role'] not in {'method_specification','candidate'} for s in request['selections'])):
            raise ValueError('FP view compilation must remain readonly target-framework-only')
    if request.get('dbc_review'):
        if not policy.dbc_evidence_refinement or stage!='repair' or target!='dbc':raise ValueError('dedicated DBC review is disabled or has the wrong object/stage')
    selected=[entries[s['material_id']] for s in request['selections']]
    lineage={}
    pending=list(selected)
    while pending:
        e=pending.pop()
        if e['id'] in lineage:continue
        lineage[e['id']]=e
        pending.extend(entries[id] for id in e['parents'])
    if not policy.source_framework_lifting:
        if any(s['role']=='source_fp' for s in request['selections']) or any(
                e['kind'] in {'fp_shared','fp_task_local'} and e['os_role']=='source' for e in lineage.values()):
            raise ValueError('source framework lifting is disabled')
        if stage=='fp_build' or stage=='repair' and target=='fp':
            if any(e['os_role']=='source' for e in selected):raise ValueError('source FP calls are disabled')
    if not policy.dbc_evidence_refinement and dbc_candidate_seen and not view_only and (stage=='dbc_recover' or stage=='repair' and target=='dbc'):
        raise ValueError('DBC refinement after the first candidate is disabled')
    if not policy.binding_plan:
        if stage=='bp_build' or stage=='repair' and target=='bp':raise ValueError('BP construction/repair is disabled')
        if any(e['kind']=='bp_candidate' for e in lineage.values()):raise ValueError('disabled BP cannot enter context or parent lineage')
        if stage=='code_generate' or stage=='repair' and target=='code':
            if request.get('generation_mode')!='without_binding':raise ValueError('direct generation must use its explicit protocol mode')
    elif request.get('generation_mode')=='without_binding':raise ValueError('full path requires BP')
    if not policy.candidate_contract_repair and stage=='repair' and target=='code':
        for s in request['selections']:
            if s['role'] not in {'internal_diagnostic','validation_report'}:continue
            e=entries[s['material_id']]
            pending=list(e['parents']);seen=set()
            candidate_dependent=False
            while pending:
                id=pending.pop()
                if id in seen:continue
                seen.add(id);ancestor=entries[id]
                candidate_dependent|=ancestor['kind']=='candidate_code'
                pending.extend(ancestor['parents'])
            if (s['role']=='internal_diagnostic' or candidate_dependent) and e.get('producer') not in {'approved_compile_tool','method_edit_checker'}:
                raise ValueError('compile-only repair forbids candidate contract diagnostics')
    return policy
