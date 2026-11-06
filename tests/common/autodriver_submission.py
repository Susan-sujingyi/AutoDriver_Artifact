"""Collect a frozen host candidate before any independent evaluation.

No evaluator, reference or case data is read here. Missing submission remains
an explicit terminal method outcome rather than an empty-patch score.
"""
from pathlib import Path
import json

from benchmark.common.api import save
from tests.common.autodriver_json import load
from tests.common.execution.artifacts import read_regular
from method.autodriver.implementation.system.protocol.assembler import canonical,digest
from method.autodriver.implementation.system.variants import variant


def collect_frozen(execution,destination,variant_name):
    variant(variant_name)
    execution=Path(execution);destination=Path(destination)
    report=load(execution/'report.json')
    if report.get('cleanup',{}).get('status')!='pass':
        raise ValueError('method cleanup is required before final collection')
    freeze=report.get('candidate_freeze')
    if freeze is None:
        if (report.get('final_submission') is not None or (execution/'candidate-freeze.json').exists()
                or (execution/'final-submission').exists()):
            raise ValueError('inconsistent frozen candidate handoff')
        return None
    if report.get('final_submission')!='final-submission':raise ValueError('fixed host submission handoff required')
    saved=json.loads(read_regular(execution/'candidate-freeze.json'))
    content={key:value for key,value in freeze.items() if key!='identity'}
    if (saved!=freeze or freeze.get('schema')!='autodriver.frozen-candidate/v1'
            or freeze.get('identity')!=digest(canonical(content).encode())
            or freeze.get('method_variant')!=variant_name or freeze.get('independent_scoring')!='not_run'
            or freeze.get('hardware_status')!='not_run'):
        raise ValueError('final candidate freeze identity/variant/scope mismatch')
    source=execution/'final-submission'
    if source.is_symlink() or not source.is_dir():raise ValueError('regular host submission directory required')
    # The canonical handoff contains exactly the patch and its manifest.
    if {p.name for p in source.iterdir()}!={'manifest.json','driver.patch'}:
        raise ValueError('unexpected files in final host submission')
    payloads={name:read_regular(source/name) for name in ('manifest.json','driver.patch')}
    for name,field in [('manifest.json','submission_manifest_sha256'),('driver.patch','patch_sha256')]:
        if digest(payloads[name])!=freeze.get(field):raise ValueError('frozen submission bytes changed')
    destination.mkdir(parents=True,exist_ok=False)
    for name in ('manifest.json','driver.patch'):
        (destination/name).write_bytes(payloads[name])
    save(destination.parent/'candidate-freeze.json',freeze)
    return freeze


def no_submission_report(task_id,validation_identity,execution):
    from benchmark.common.evaluation.report import new_report,attach_generation,stage
    report=new_report(task_id,validation_identity)
    attach_generation(report,execution,submission_state='no_submission')
    complete=execution.get('termination',{}).get('kind') in {'completed','method_limit'}
    report.update(status='not_completed' if complete else 'infrastructure_error',
        generation_outcome='no_submission' if complete else 'no_candidate_after_interruption',trial_execution_complete=complete,
        independent_scoring='not_run',reason='method finished without an eligible frozen candidate')
    if execution.get('termination',{}).get('kind')=='completed':
        finding=stage('generation','failed','method_no_submission','candidate','confirmed',
            [{'path':'../execution/report.json','pointer':'/result'}])
        report['stages']=[finding if item['stage']=='generation' else item for item in report['stages']]
        report.update(failure=finding,result_class='candidate_failure')
    return report
