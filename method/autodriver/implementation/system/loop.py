"""Method-owned compile/check/repair control with a single cumulative bound."""
from .protocol.assembler import ProtocolError,write_private_json


def execute(pipeline,checker,*,development_partial=False):
    result=pipeline.run(stop_after='prototype' if development_partial else None)
    if not pipeline.code_proposal:return result
    observations=[];status='candidate_checks_unknown';repair_stop=None
    def stopped(error,target):
        return {'error_type':type(error).__name__,'reason':str(error),'target':target,
            **{key:getattr(error,key) for key in ('kind','limit','used') if hasattr(error,key)}}
    for attempt in range(5):
        observation=checker(pipeline.workspace.root,pipeline.code_proposal)
        if (observation.get('schema')!='autodriver.candidate-check/v1'
                or observation.get('revision')!=pipeline.code_proposal['revision']
                or observation.get('independent_scoring')!='not_run' or observation.get('hardware_status')!='not_run'):
            raise ValueError('unapproved candidate-check response')
        observations.append(observation);write_private_json(pipeline.output/'candidate-observations.json',observations)
        if observation.get('status')!='completed':status='candidate_tool_incomplete';break
        diagnostics=observation.get('repair_diagnostics',[])
        upstream=observation.get('upstream_diagnostics',[])
        if upstream:
            if not pipeline.policy.candidate_contract_repair:
                raise ValueError('candidate contract feedback is disabled')
            if pipeline.code_repairs_used()>=4 or attempt==4:status='candidate_repair_bound_exhausted';break
            old=pipeline.code_proposal
            try:
                if not pipeline.repair_candidate_prerequisite(upstream):
                    status='candidate_upstream_unresolved';break
                # This regeneration remains a code repair against the original
                # baseline and consumes the same cumulative four-request cap.
                diagnostics=upstream+diagnostics
                result=pipeline.repair_code(diagnostics,prior_proposal=old)
            except ProtocolError as error:
                repair_stop=stopped(error,'upstream')
                status='candidate_upstream_response_rejected';break
            if pipeline.code_proposal is None or pipeline.code_proposal['revision']==old['revision']:
                status='candidate_repair_no_candidate';break
            continue
        if not diagnostics:
            if observation.get('compile_status')!='pass':status='candidate_compile_incomplete'
            elif not pipeline.policy.candidate_contract_repair:status='candidate_compiled'
            elif observation.get('native_checks_status')=='satisfied':status='candidate_internal_checks_satisfied'
            else:status='candidate_checks_unknown'
            break
        if pipeline.code_repairs_used()>=4 or attempt==4:status='candidate_repair_bound_exhausted';break
        prior=pipeline.code_proposal['revision']
        try:
            result=pipeline.repair_code(diagnostics)
        except ProtocolError as error:
            repair_stop=stopped(error,'code')
            status='candidate_repair_response_rejected';break
        if pipeline.code_proposal['revision']==prior:status='candidate_repair_no_candidate';break
    no_submission=status in {'candidate_repair_response_rejected','candidate_repair_no_candidate',
        'candidate_upstream_unresolved','candidate_upstream_response_rejected'}
    final=pipeline.result(status,'candidate_checks')
    final.update(candidate=None if no_submission else 'host_managed_final_candidate',candidate_checks=observations,
                 final_candidate_disposition='no_submission_after_latest_repair' if no_submission else 'last_applied_candidate',
                 code_repairs_used=pipeline.code_repairs_used(),
                 scope='listed public build and finite method-native checks only; final source freezes on host before independent scoring')
    if repair_stop is not None:final['repair_stop']=repair_stop
    write_private_json(pipeline.output/'pipeline-result.json',final)
    return final
