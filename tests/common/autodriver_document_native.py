"""Run existing method-native checkers when derived executable views exist."""
from benchmark.common.api import save
from method.autodriver.implementation.system.document_projection import project_native_state
from method.autodriver.implementation.system.contract.model import ContractError
from tests.common.autodriver_json import load


def document_native(executor, candidate, destination, state, *, deadline, binding_plan,framework_executor=None):
    projection, report = project_native_state(state, binding_plan=binding_plan)
    if projection is None:
        destination.mkdir(parents=True, exist_ok=False)
        result = dict(status='not_checked', reason='executable contract view unavailable',
            projection=report, native_candidate_executed=False, independent_scoring='not_run', hardware_status='not_run')
    else:
        try:
            result = executor(candidate, destination, projection, deadline=deadline)
        except ContractError as error:
            # An observer/checker can reject after the candidate ran. Retain
            # its actual execution and cleanup evidence instead of reporting
            # that no execution occurred or silently dropping cleanup scope.
            build=destination/'build-process/process.json'
            execution=destination/'run-process/process.json'
            cases=sorted(destination.glob('case-*/process.json'))
            executed=execution.is_file() or bool(cases)
            result = dict(status='checker_error' if executed else 'unknown', reason=str(error), error_type=type(error).__name__,
                          native_candidate_executed=executed, independent_scoring='not_run', hardware_status='not_run')
            if build.is_file():result['build']=load(build)
            if execution.is_file():result['execution']=load(execution)
            if cases:result['executions']=[load(path) for path in cases]
        result['projection'] = report
        # A view can be internally consistent without being entailed by the
        # primary document; this limitation never disappears after execution.
        result['primary_document_entailment'] = 'unverified'
    if (framework_executor is not None and not result.get('native_candidate_executed')
            and result['status'] in {'not_checked','unknown'}):
        save(destination/'device-plan-gap.json',result)
        probe=destination/'framework-probe'
        try:
            # The fault probe resolves its own optional FP view and validates
            # that view against the primary material. The device projection
            # replaces object material IDs, so passing it here breaks that
            # binding whenever both compiled views exist but lowering fails.
            fallback=framework_executor(candidate,probe,state,deadline=deadline)
        except ContractError as error:
            build=probe/'build-process/process.json';execution=probe/'run-process/process.json'
            cases=sorted(probe.glob('case-*/process.json'));executed=execution.is_file() or bool(cases)
            fallback=dict(status='checker_error' if executed else 'unknown',reason=str(error),
                error_type=type(error).__name__,native_candidate_executed=executed)
            if build.is_file():fallback['build']=load(build)
            if execution.is_file():fallback['execution']=load(execution)
            if cases:fallback['executions']=[load(p) for p in cases]
        fallback['device_contract_execution']=result
        fallback['projection']=report
        fallback['primary_document_entailment']='unverified'
        fallback['observation_path']='framework-probe/run-process/stdout.jsonl'
        result=fallback
    save(destination/'native-report.json', result)
    return result
