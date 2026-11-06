"""Separate necessary upstream checks from checks of a future candidate.

This schedule is method-owned. Neither a model-supplied phase nor an overall
report label can exempt an unresolved prerequisite. Reports retain all unknowns.
"""


def generation_admission(checks,*,binding_plan=True,contracts=None,support_audits=None):
    from .model import identity,ContractError
    from .scope import framework_scope,path_required
    required=['dbc','target_fp']+(['bp','binding'] if binding_plan else [])
    blockers=[];deferred=[];observed=[];limitations=[];unused=[];candidate_framework=[];dependencies=None
    contracts=contracts or {};support_audits=support_audits or {}
    if contracts:
        try:
            dependencies=framework_scope(contracts['dbc'],contracts['target_fp'],contracts.get('bp') if binding_plan else None)
        except (ContractError,KeyError,TypeError) as error:
            blockers.append({'object':'target_fp','subject':'','status':'unknown','check':'framework_dependency_scope',
                             'detail':str(error)})
    for key in required:
        report=checks.get(key)
        if not isinstance(report,dict) or report.get('status') not in {'satisfied','unknown','violated'}:
            blockers.append({'object':key,'subject':'','status':'unknown','check':'missing_upstream_report',
                             'detail':'a current semantic report is required'})
            continue
        findings=report.get('findings',[])
        if not isinstance(findings,list):raise ValueError('semantic findings must be a list')
        parent_key='bp' if key=='binding' else key
        if contracts and (parent_key not in contracts or report.get('contract_sha256')!=identity(contracts[parent_key])):
            blockers.append({'object':key,'subject':'','status':'unknown','check':'current_contract_report',
                             'detail':'semantic report is not pinned to the current contract'})
        if contracts and key in {'dbc','target_fp','bp'}:
            audit=support_audits.get(key,{})
            if (key not in contracts or audit.get('schema')!='autodriver.recovery-support/v1'
                    or audit.get('contract_sha256')!=identity(contracts[key]) or audit.get('status')!='satisfied'):
                blockers.append({'object':key,'subject':'evidence','status':'unknown','check':'bounded_recovery_support',
                    'detail':str(audit.get('gaps','a current pinned source/claim/workflow audit is required'))})
        statuses=set()
        for finding in findings:
            if finding.get('status') not in {'satisfied','unknown','violated'}:raise ValueError('invalid semantic finding status')
            item={**finding,'object':key};statuses.add(item['status']);observed.append(item)
            if item['status']=='satisfied':continue
            if (key,item.get('subject'),item.get('check'),item['status'])==(
                    'binding','candidate','implementation_conformance','unknown'):
                deferred.append(item)
            elif (item['status']=='unknown' and item.get('subject')=='evidence' and item.get('check')=='evidence_entailment'
                  and key in {'dbc','target_fp','bp'} and key in contracts
                  and support_audits.get(key,{}).get('schema')=='autodriver.recovery-support/v1'
                  and support_audits[key].get('contract_sha256')==identity(contracts[key])
                  and support_audits[key].get('status')=='satisfied'
                  and support_audits[key].get('evidence_entailment')=='unknown'):
                limitations.append({**item,'recovery_support':support_audits[key],
                    'disposition':'universal entailment is not a generation prerequisite; source/claim/workflow audit checked separately'})
            elif not binding_plan and key=='target_fp' and item['status']=='unknown':
                # With no BP there is no pre-generation API selection. Keep
                # unresolved framework facts visible until the candidate
                # selects actual native APIs; do not require unrelated APIs
                # to become a complete OS profile before direct generation.
                candidate_framework.append({**item,'disposition':
                    'without BP: unresolved framework facts require candidate API selection and subsequent internal checks; no guarantee established'})
            elif (item['status']=='unknown' and dependencies is not None
                  and ((key=='target_fp' and item.get('check') in {'coverage','framework_slot_type','provider'}
                        and not path_required(item.get('subject',''),dependencies))
                       or (key=='binding' and item.get('check')=='inherited_parent_gap'
                           and item.get('subject','').startswith('fp.')
                           and not path_required(item['subject'][3:],dependencies)))):
                unused.append({**item,'disposition':'outside current conservative framework dependency projection'})
            else:blockers.append(item)
        # Opaque or aggregate-only failure/unknown is never vacuously admitted.
        if report['status']!='satisfied' and report['status'] not in statuses:
            blockers.append({'object':key,'subject':'','status':report['status'],'check':'unexplained_upstream_status',
                             'detail':'report has no finding accounting for its unresolved overall status'})
    status='violated' if any(item['status']=='violated' for item in blockers) else 'unknown' if blockers else 'satisfied'
    coverage='violated' if any(item['status']=='violated' for item in observed+blockers) else 'unknown' if deferred or blockers or limitations or unused or candidate_framework else 'satisfied'
    return {'schema':'autodriver.generation-admission/v2','status':status,'coverage_status':coverage,
            'required_reports':required,'blocking_findings':blockers,'post_generation_obligations':deferred,
            'assurance_limitations':limitations,'unused_framework_findings':unused,'framework_dependencies':dependencies,
            'candidate_framework_obligations':candidate_framework,
            'candidate_conformance':'not_checked',
            'scope':'generation prerequisites only; deferred candidate execution is still unknown and must be checked after generation'}
