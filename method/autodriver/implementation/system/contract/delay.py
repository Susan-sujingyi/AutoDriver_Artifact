"""Synchronous-delay arithmetic and exact parent bindings, without OS defaults."""
import ast
from fractions import Fraction

from .model import Node,Unknown,ContractError,unwrap
from .data import integer
from .expression import quantity,seconds,check_pure_expression


def lower_bound(guarantee,argument=None):
    guarantee=unwrap(guarantee)
    if not isinstance(guarantee,Node) or guarantee.name!='ElapsedTimeLowerBound':
        raise Unknown('synchronous API has no typed elapsed-time lower bound')
    unit=seconds(quantity(1,unwrap(guarantee['argument_unit'])))
    if unit<=0:raise ContractError('delay argument unit must be positive')
    limits=unwrap(guarantee['argument_range'])
    if not isinstance(limits,(tuple,list)) or len(limits)!=2:raise ContractError('delay argument range requires two integer endpoints')
    low,high=(integer(unwrap(value),0,2**128-1) for value in limits)
    if high<low:raise ContractError('empty delay argument range')
    loss=seconds(unwrap(guarantee['early_loss']))
    condition=unwrap(guarantee['success_condition'])
    if not isinstance(condition,str) or not condition or len(condition)>2048:raise ContractError('bounded delay success predicate required')
    check_pure_expression(condition)
    names={item.id for item in ast.walk(ast.parse(condition,mode='eval')) if isinstance(item,ast.Name)}
    if names-{'result'}:raise ContractError('delay success condition may reference only result')
    # Do not invent an errno convention or treat every synchronous return as
    # success. The code checker must apply this exact recovered predicate.
    if 'result' not in names:raise ContractError('delay success predicate must distinguish return results')
    if unwrap(guarantee['interruption'])!='failure':raise Unknown('early interruption is not established as a non-success return')
    if argument is None:return {'unit_seconds':unit,'range':(low,high),'early_loss_seconds':loss,'success_condition':condition}
    argument=integer(unwrap(argument),low,high)
    return {'lower_seconds':max(Fraction(0),argument*unit-loss),'argument':argument,
        'success_condition':condition,'scope':'declared successful-return lower bound only; no source entailment, actual provider, candidate branch/order or upper-bound proof'}


def check_binding(operation,path,binding,references):
    def from_fp(value):
        if not isinstance(value,Node) or value.name!='FromFP':raise ContractError('delay APIs must reference the current parent FP')
        result=unwrap(references.resolve('fp.'+value['path'].removeprefix('fp.')))
        if not isinstance(result,Node) or result.name!='APIContract':raise ContractError('delay API reference must resolve to APIContract')
        completion=unwrap(result['completion'])
        if not isinstance(completion,Node) or completion.name!='SyncReturn':raise Unknown('delay/anchor API is not a recovered synchronous return')
        return result,completion
    api,completion=from_fp(binding['call']);anchor_api,anchor_completion=from_fp(binding['anchor_call'])
    anchor=unwrap(binding['anchor'])
    if not isinstance(anchor,Node) or anchor.name!='EventBinding':raise ContractError('synchronous delay needs an exact event binding')
    expected=operation['of'] if operation.name=='WaitAfterCompletion' else operation['anchor']
    if isinstance(expected,str):
        from .model import node
        expected=node('EventRef',operation=expected,phase='physical_success',instance='current')
    if not isinstance(expected,Node) or expected.name!='EventRef':raise Unknown('wait lacks a typed current anchor')
    if expected['phase']!='physical_success' or expected['instance']!='current':
        raise Unknown('synchronous delay projection currently binds current physical completion only')
    if anchor['device_event']!=expected:raise ContractError('delay event binding does not reference the current DBC wait anchor')
    native=anchor['native_event']
    if not isinstance(native,Node) or native.name!='EventRef' or native['instance']!='current' or native['phase']!='logical_success' or native['operation']!=anchor_api['symbol']:
        raise ContractError('synchronous anchor must be the current referenced API successful return')
    if anchor['assumptions']:raise Unknown('delay anchor assumptions remain unestablished')
    relation=unwrap(anchor_completion['physical_completion_order'])
    if not isinstance(relation,str) or relation not in {'equal','device_not_after_native'}:raise Unknown('parent FP does not establish physical completion before the synchronous anchor')
    if anchor['relation']!=relation:raise ContractError('delay anchor relation disagrees with parent FP')
    duration=binding['duration']
    if isinstance(duration,Node) and duration.name=='FromDBC':
        expected_path=path+'.minimum'
        if duration['path'].removeprefix('dbc.')!=expected_path:raise ContractError('delay duration must reference this exact DBC minimum')
        minimum=references.resolve('dbc.'+expected_path)
    elif isinstance(duration,Node) and duration.name=='MaximumApplicableDBCBound':minimum=operation['minimum']
    else:raise ContractError('delay duration must reference the current DBC minimum')
    if binding['on_interruption']!='fail_current_operation':raise ContractError('interrupted delay cannot continue as successful acquisition')
    result=lower_bound(completion['delay'],binding['argument'])
    required=seconds(unwrap(minimum))
    if result['lower_seconds']<required:raise ContractError('synchronous delay permits an earlier continuation than the current device minimum')
    if operation['maximum'] is not None:raise Unknown('synchronous scheduling does not establish the required physical upper bound')
    return {**result,'required_seconds':str(required),'lower_seconds':str(result['lower_seconds']),
        'api':api['symbol'],'anchor_api':anchor_api['symbol'],'provider_assurance':'source_claim_required',
        'candidate_order_and_interruption':'not_checked'}
