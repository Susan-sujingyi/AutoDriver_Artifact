import os

from method.common.adapter import run_contract,validate_live,UnsupportedMethod


def run(request):
    if request.get('operation')=='offline_contract':return run_contract('autodriver',request)
    if request.get('condition')!='SD':raise UnsupportedMethod('new autodriver live algorithm admits SD only')
    workspace=validate_live('autodriver',request)
    from .implementation.system.document_pipeline import DocumentPipeline as Pipeline
    from .implementation.system.document_loop import execute
    from .implementation.system.candidate_client import check_candidate
    variant=os.environ.get('AUTODRIVER_METHOD_VARIANT','full')
    pipeline=Pipeline(workspace,variant_name=variant,maximum_calls=min(64,int(request['budget'].get('requests',64))))
    result=execute(pipeline,check_candidate)
    return {'schema':'autodriver.method-result/v1','method':'autodriver','task_id':request['task_id'],'condition':'SD',
        'status':result['status'],'variant':variant,'candidate':result.get('candidate'),'collection':'host_canonical_patch',
        'code_repairs_used':pipeline.code_repairs_used(),'hardware_status':'not_run','pipeline':result,
        'scope':'semantic documents with advisory contract findings; listed public compiler/finite internal checks; live release status is separate'}
