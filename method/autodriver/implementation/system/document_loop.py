"""Advisory-contract compile/check/repair with last-applied submission policy."""
from copy import deepcopy

from .protocol.assembler import ProtocolError, write_private_json


def execute(pipeline, checker):
    initial = pipeline.run()
    if not pipeline.code_proposal:
        return initial
    observations = []
    status, repair_stop = 'candidate_checks_unknown', None
    for attempt in range(5):
        proposed = pipeline.code_proposal
        observation = checker(pipeline.workspace.root, proposed)
        if (observation.get('schema') != 'autodriver.candidate-check/v1'
                or observation.get('revision') != proposed['revision']
                or observation.get('applied_revision') != proposed['revision']
                or observation.get('independent_scoring') != 'not_run'
                or observation.get('hardware_status') != 'not_run'):
            raise ValueError('unapproved advisory candidate-check response')
        pipeline.last_applied_proposal = deepcopy(proposed)
        observations.append(observation)
        write_private_json(pipeline.output/'candidate-observations.json', observations)
        if observation.get('status') != 'completed':
            status = 'candidate_tool_incomplete'
            break
        diagnostics = observation.get('repair_diagnostics', [])
        upstream = observation.get('upstream_diagnostics', [])
        if not pipeline.policy.candidate_contract_repair:
            if upstream or any(d.get('producer') != 'approved_compile_tool' for d in diagnostics):
                raise ValueError('compile-only received forbidden contract feedback')
        if not upstream and not diagnostics:
            status = ('candidate_compile_incomplete' if observation.get('compile_status') != 'pass' else
                      'candidate_compiled' if not pipeline.policy.candidate_contract_repair else
                      'candidate_internal_checks_satisfied' if observation.get('native_checks_status') == 'satisfied' else
                      'candidate_checks_unknown')
            break
        if pipeline.code_repairs_used() >= 4 or attempt == 4:
            status = 'candidate_repair_bound_exhausted'
            break
        previous = proposed['revision']
        try:
            if upstream and not pipeline.repair_candidate_prerequisite(upstream):
                status = 'candidate_upstream_unresolved'
                break
            pipeline.repair_code(upstream + diagnostics, prior_proposal=proposed)
        except ProtocolError as error:
            repair_stop = dict(error_type=type(error).__name__, reason=str(error),
                **{k:getattr(error,k) for k in ('kind','limit','used') if hasattr(error,k)})
            status = 'candidate_repair_response_rejected'
            break
        if pipeline.code_proposal is None or pipeline.code_proposal['revision'] == previous:
            status = 'candidate_repair_no_candidate'
            break
    applied = pipeline.last_applied_proposal
    final = pipeline.result(status, 'candidate_checks')
    final.update(candidate='host_managed_final_candidate', candidate_checks=observations,
        code_proposal=applied, final_candidate_disposition='last_applied_candidate',
        selection_policy='last actually applied revision; no best-of or scoring selection',
        current_proposal_revision=pipeline.code_proposal['revision'] if pipeline.code_proposal else None,
        applied_parent_revisions=pipeline.revisions.records[applied['revision']].parents,
        applied_parents_still_current=all(pipeline.revisions.current.get(k)==v
            for k,v in pipeline.revisions.records[applied['revision']].parents.items()),
        scope='last applied source after bounded public/internal checks; unsupported checks stay unknown; host freeze precedes independent scoring')
    if repair_stop is not None:
        final['repair_stop'] = repair_stop
    write_private_json(pipeline.output/'pipeline-result.json', final)
    return final
