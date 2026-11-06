"""Audit bounded recovery support without claiming semantic entailment.

The caller supplies only already validated, normalized response citations and
the pinned method evidence registry. This checks sources and workflow, not the
truth of a model's assertion. No model-supplied assurance flag is consumed.
"""
from .model import ContractError, identity, walk


ALLOWED = {
    'fp': {'framework_source', 'framework_document', 'framework_scope'},
    'dbc': {'device_source', 'document_extract',
            'source_configuration', 'public_task_projection', 'public_target_support',
            'source_analysis', 'fp_shared', 'fp_task_local', 'framework_source', 'framework_document'},
    'bp': {'dbc_candidate', 'fp_shared', 'fp_task_local', 'public_target_support',
           'public_task_projection', 'framework_source', 'framework_document', 'framework_scope'},
}


def audit_support(root, kind, links, evidence, *, evidence_refined=False, refinement_required=False):
    records=[];gaps=[];claims=set()
    if kind not in ALLOWED:raise ContractError('unsupported evidence consumer')
    if root.name!={'fp':'FrameworkProfile','dbc':'DeviceContract','bp':'BindingPlan'}[kind]:
        raise ContractError('evidence audit consumer does not match the contract root')
    if not isinstance(links,(list,tuple)) or not 1<=len(links)<=1024:
        gaps.append('bounded nonempty normalized evidence links are required')
        links=[]
    for index,link in enumerate(links):
        try:
            if not isinstance(link,dict) or set(link)!={'claim','basis','material_id','start_line','end_line'}:
                raise ContractError('normalized response citation required')
            if any(not isinstance(link[key],str) or not link[key].strip() for key in ('claim','basis','material_id')):
                raise ContractError('nonempty assertion, basis and material identity required')
            entry=evidence.registry[link['material_id']]
            if entry['kind'] not in ALLOWED[kind]:raise ContractError('citation is not a permitted support kind for '+kind)
            data=evidence.read(link['material_id'])  # Includes hash/approved-lineage checks.
            start,end=link['start_line'],link['end_line']
            if type(start) is not int or type(end) is not int or not 1<=start<=end<=len(data.decode('utf8').splitlines()):
                raise ContractError('citation range does not resolve to pinned source lines')
            claims.add(link['claim'])
            records.append(dict(link,kind=entry['kind'],sha256=entry['sha256']))
        except (ContractError,KeyError,UnicodeError,TypeError) as error:
            gaps.append('link '+str(index)+': '+str(error))
    for path,value in walk(root):
        if value.name=='Constraint':
            labels=value['evidence']
            if not isinstance(labels,(tuple,list)) or not labels or any(not isinstance(claim,str) or claim not in claims for claim in labels):
                gaps.append(path+': Constraint.evidence must name this response\'s claim labels, not material IDs or citation handles')
    if kind=='fp' and not any(record['kind']=='framework_source' for record in records):
        gaps.append('framework recovery needs direct frozen framework source support')
    if kind=='dbc':
        for source in ('device_source','document_extract'):
            if any(entry['kind']==source for entry in evidence.registry.values()) and not any(record['kind']==source for record in records):
                gaps.append('device recovery has no direct '+source+' support')
        if refinement_required and not evidence_refined:gaps.append('the trusted dedicated source/manual review has not executed')
    if kind=='bp' and not all(any(record['kind'] in kinds for record in records)
                            for kinds in ({'dbc_candidate'},{'fp_shared','fp_task_local'},{'public_target_support','public_task_projection'})):
        gaps.append('binding recovery must cite each read-only device/framework/integration parent')
    return {'schema':'autodriver.recovery-support/v1','kind':kind,'contract_sha256':identity(root),
        'status':'unknown' if gaps else 'satisfied','gaps':gaps,'citations':records,
        'dedicated_review_executed':bool(evidence_refined),'refinement_required':bool(refinement_required),
        'assurance':'model_assisted_recovery_with_pinned_citations','evidence_entailment':'unknown',
        'scope':'source identity, citation resolution, claim labels and recovery workflow only; neither assertion truth nor universal entailment is proved'}
