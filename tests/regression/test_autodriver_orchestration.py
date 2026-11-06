"""Real call assembly with a fake client, interventions and revision boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.calls import CallRunner,merge_selections,decode_response
from method.autodriver.implementation.system.variants import admit_call,VARIANTS
from method.autodriver.implementation.system.revisions import Revisions
from method.autodriver.implementation.system.protocol.assembler import ProtocolError,assemble,digest
from method.autodriver.implementation.system.contract.model import ContractError,node,identity,parse
from method.autodriver.implementation.system.contract.solver import prove
from method.autodriver.implementation.system.contract.binding import check_binding
from method.autodriver.implementation.system.code import apply_edits


class CallsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.visible,self.registry,self.requests,self.responses=make_fixture(self.tmp.name)

    def test_actual_five_calls_and_four_repair_objects_without_network(self):
        for name in self.requests:
            def client(prompt,*,system,stage,step):
                data=json.loads(prompt)
                self.assertIn('Ref(identifier)',system)
                self.assertEqual(data['output_template']['stage'],stage)
                return json.dumps(self.responses[name])
            runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/name,client=client)
            response,check=runner.call(self.requests[name])
            self.assertTrue(check['transport_valid'])
            self.assertEqual(check['semantic_validation']['status'],'unknown')
            self.assertTrue(list(runner.output.glob('*.messages.json')))
            with self.assertRaises(ProtocolError):runner.call(self.requests[name])
        for kind,stage,id in [('fp','fp_build','target_fp'),('dbc','dbc_recover','dbc'),('bp','bp_build','bp')]:
            reg=deepcopy(self.registry)
            next(e for e in reg['materials'] if e['id']=='diagnostic')['parents']=[id]
            request=deepcopy(self.requests[stage]);request.update(stage='repair',repair_target=kind,call_id='repair-'+kind)
            request['selections'] += [dict(role='candidate',material_id=id),dict(role='internal_diagnostic',material_id='diagnostic')]
            response=deepcopy(self.responses[stage]);response.update(stage='repair',call_id=request['call_id'])
            runner=CallRunner(Evidence(self.visible,reg),Path(self.tmp.name)/('repair-'+kind),client=lambda *a,**kw:json.dumps(response))
            self.assertTrue(runner.call(request)[1]['transport_valid'])

    def test_only_executable_tools_are_advertised(self):
        messages,_=assemble(self.visible,self.registry,self.requests['fp_build'])
        self.assertEqual(json.loads(messages[1]['content'])['available_tool_names'],[])

    def test_envelope_normalization_never_changes_constructor_strings(self):
        text='{"text":"literal comma,} and escaped quote \\\" ,]", "list":[1,2,],}'
        response,audit=decode_response(text)
        self.assertEqual(response['text'],'literal comma,} and escaped quote " ,]')
        self.assertEqual(response['list'],[1,2]);self.assertEqual(len(audit['removed_character_offsets']),2)
        self.assertIsNone(decode_response(json.dumps(response))[1])
        for bad in ('{"x":1,"x":2}', '{"x":missing,}', 'prefix {"x":1,}', '{"x":"unterminated,}'):
            with self.subTest(bad=bad),self.assertRaises(ProtocolError):decode_response(bad)

    def test_malformed_contract_uses_bounded_same_object_repair(self):
        stages=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt);call=data['call'];stages.append(call['stage'])
            response=deepcopy(self.responses['fp_build']);response.update(call_id=call['call_id'],stage=call['stage'])
            if call['stage']=='fp_build':response['artifact']['content']="FrameworkProfile('ExampleTarget', Ref('target_framework')"
            else:
                self.assertEqual(call['repair_target'],'fp')
                self.assertTrue(any(c['role']=='candidate' for c in data['context']))
                self.assertTrue(any(c['role']=='internal_diagnostic' for c in data['context']))
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'syntax-repair',client=client)
        response,checked,history=runner.recover(self.requests['fp_build'])
        self.assertEqual(stages,['fp_build','repair']);self.assertEqual(len(history),2)
        self.assertEqual(response['artifact']['kind'],'fp');self.assertTrue(checked['transport_valid'])

    def test_tree_carrier_runs_through_actual_assembly_and_normalization(self):
        request=deepcopy(self.requests['fp_build']);request['contract_transport']='constructor_tree'
        def client(prompt,**kwargs):
            data=json.loads(prompt);call=data['call'];self.assertEqual(data['output_template']['artifact']['format'],'constructor_tree')
            response=deepcopy(self.responses['fp_build']);response.update(call_id=call['call_id'],stage=call['stage'])
            response['artifact']={'kind':'fp','format':'constructor_tree','content':{'$constructor':'FrameworkProfile',
                'args':['ExampleTarget',{'$constructor':'Ref','args':['target_framework'],'fields':{}}],'fields':{}}}
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'tree-carrier',client=client)
        response,checks,_=runner.recover(request)
        self.assertEqual(response['artifact']['format'],'constructor');self.assertTrue(checks['transport_valid'])
        self.assertTrue(list(runner.output.glob('*.constructor-normalization.json')))

    def test_concatenated_envelopes_are_opaque_bounded_repairs(self):
        stages=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt);call=data['call'];stages.append(call['stage'])
            response=deepcopy(self.responses['fp_build']);response.update(call_id=call['call_id'],stage=call['stage'])
            if call['stage']=='fp_build':return '{}'+json.dumps(response)
            diagnostic=json.loads(next(item['text'] for item in data['context'] if item['role']=='internal_diagnostic'))
            self.assertTrue(diagnostic['opaque_invalid_envelope']);self.assertIsNone(diagnostic['original_evidence_links'])
            self.assertEqual(call['repair_target'],'fp')
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'opaque-repair',client=client)
        self.assertTrue(runner.recover(self.requests['fp_build'])[1]['transport_valid'])
        self.assertEqual(stages,['fp_build','repair'])

    def test_code_citation_failure_uses_bounded_code_only_repair(self):
        stages=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt);call=data['call'];stages.append(call['stage'])
            response=deepcopy(self.responses['code_generate']);response.update(call_id=call['call_id'],stage=call['stage'])
            if call['stage']=='code_generate':response['evidence_links'][0].update(start_line=99999,end_line=99999)
            else:
                self.assertEqual(call['repair_target'],'code')
                candidate=next(c for c in data['context'] if c['role']=='candidate')
                self.assertEqual(json.loads(candidate['text'])['kind'],'code')
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'code-citation-repair',client=client)
        response,checks,_=runner.recover(self.requests['code_generate'])
        self.assertEqual(stages,['code_generate','repair']);self.assertTrue(checks['transport_valid']);self.assertEqual(response['artifact']['kind'],'code')

    def test_seeded_fp_protocol_repair_omits_build_only_existing_role(self):
        request=deepcopy(self.requests['fp_build'])
        request['selections'].append({'role':'existing_fp','material_id':'target_fp'})
        def client(prompt,**kwargs):
            data=json.loads(prompt);call=data['call'];response=deepcopy(self.responses['fp_build'])
            response.update(call_id=call['call_id'],stage=call['stage'])
            if call['stage']=='fp_build':response['artifact']['content']="FrameworkProfile('broken'"
            else:
                self.assertEqual(call['repair_target'],'fp')
                self.assertNotIn('existing_fp',[c['role'] for c in data['context']])
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'seed-repair',client=client)
        self.assertTrue(runner.recover(request)[1]['transport_valid'])
        self.assertEqual(len(runner.calls),2)

    def test_protocol_repair_limit_does_not_reset_call_budget(self):
        def client(prompt,**kwargs):
            call=json.loads(prompt)['call'];response=deepcopy(self.responses['fp_build'])
            response.update(call_id=call['call_id'],stage=call['stage']);response['artifact']['content']="FrameworkProfile('broken'"
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'limited-repair',client=client,maximum_repairs=1)
        with self.assertRaises(ProtocolError):runner.recover(self.requests['fp_build'])
        self.assertEqual(len(runner.calls),2)

    def test_saved_invalid_response_is_locally_checked_then_only_repair_is_sent(self):
        request=self.requests['fp_build'];bad=deepcopy(self.responses['fp_build'])
        bad['artifact']['content']="FrameworkProfile('ExampleTarget', Ref('target_framework')"
        raw=json.dumps(bad)
        first=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'original',client=lambda *a,**k:raw)
        with self.assertRaises(ProtocolError):first.call(request)
        audit=json.loads((first.output/(request['call_id']+'.audit.json')).read_text())
        saved=dict(text=raw,response_sha256=digest(raw.encode()),prompt_sha256=audit['prompt_sha256'],origin_request_id='synthetic-original-request')
        transported=[]
        def repair(prompt,**kwargs):
            call=json.loads(prompt)['call'];transported.append(call['stage'])
            self.assertEqual(call['stage'],'repair');self.assertEqual(call['repair_target'],'fp')
            response=deepcopy(self.responses['fp_build']);response.update(call_id=call['call_id'],stage=call['stage'])
            return json.dumps(response)
        second=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'resumed',client=repair)
        self.assertTrue(second.recover(request,saved_response=saved)[1]['transport_valid'])
        self.assertEqual(transported,['repair'])
        self.assertEqual(second.calls[0]['execution_mode'],'saved_response_local_validation')
        saved['prompt_sha256']='0'*64
        mismatch=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'mismatch',client=lambda *a,**k:self.fail('network fallback'))
        with self.assertRaises(ProtocolError):mismatch.recover(request,saved_response=saved)

    def test_pending_saved_response_cannot_fall_back_to_a_different_fresh_call(self):
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'wrong-order',
            client=lambda *a,**k:self.fail('unexpected paid request fallback'))
        runner.saved_responses={'different-call-id':{'text':'unused'}}
        with self.assertRaisesRegex(ProtocolError,'call order changed'):runner.call(self.requests['fp_build'])
        self.assertEqual(runner.calls,[])

    def test_bounded_context_recovery(self):
        request=deepcopy(self.requests['dbc_recover']);response=deepcopy(self.responses['dbc_recover'])
        request['selections']=[dict(x,end_line=1) if x['role']=='source_evidence' else x for x in request['selections']]
        n=0
        def client(prompt,**kwargs):
            nonlocal n
            n+=1;data=json.loads(prompt);reply=deepcopy(response);reply['call_id']=data['call']['call_id']
            if n==1:
                reply.update(outcome='needs_evidence',artifact=None,context_requests=[dict(scope='source',query='acquire',question='Find function',reason='Need original body')])
            return json.dumps(reply)
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'recover',client=client)
        result,_,history=runner.recover(request)
        self.assertEqual(n,2);self.assertEqual(len(history),2);self.assertEqual(result['outcome'],'candidate')

    def test_context_growth_does_not_fill_unretrieved_gaps(self):
        entry=next(e for e in self.registry['materials'] if e['id']=='source')
        path=self.visible/entry['path'];path.write_text(''.join(f'line {i}\n' for i in range(1,31)))
        entry['sha256']=digest(path.read_bytes());evidence=Evidence(self.visible,self.registry)
        select=lambda a,b:dict(role='source_evidence',material_id='source',start_line=a,end_line=b)
        merged=merge_selections([select(1,3),select(20,22)],[select(25,26)],evidence)
        self.assertEqual([(s['start_line'],s['end_line']) for s in merged],[(1,3),(20,22),(25,26)])
        # Joining a later interval must also join an earlier adjacent one.
        merged=merge_selections([select(1,4),select(5,10)],[select(9,12)],evidence)
        self.assertEqual([(s['start_line'],s['end_line']) for s in merged],[(1,12)])

    def test_interventions_disable_exact_mechanism(self):
        self.assertEqual(sum(not v for v in VARIANTS['compile_only_repair'].settings().values()),1)
        with self.assertRaises(ValueError):admit_call('without_source_framework_lifting',self.requests['dbc_recover'],self.registry)
        with self.assertRaises(ValueError):admit_call('without_dbc_evidence_refinement',self.requests['dbc_recover'],self.registry,dbc_candidate_seen=True)
        with self.assertRaises(ValueError):admit_call('without_binding_plan',self.requests['bp_build'],self.registry)
        with self.assertRaises(ValueError):admit_call('compile_only_repair',self.requests['repair'],self.registry)
        reg=deepcopy(self.registry)
        next(e for e in reg['materials'] if e['id']=='diagnostic')['producer']='approved_compile_tool'
        admit_call('compile_only_repair',self.requests['repair'],reg)
        # Upstream binding diagnostics stay available to code generation.
        admit_call('compile_only_repair',self.requests['code_generate'],self.registry)

    def test_direct_generation_has_no_dummy_bp_or_bp_lineage(self):
        request=deepcopy(self.requests['code_generate']);reg=deepcopy(self.registry)
        request['selections']=[s for s in request['selections'] if s['role']!='bp']
        request['generation_mode']='without_binding'
        next(e for e in reg['materials'] if e['id']=='validation')['parents']=['dbc','target_fp']
        admit_call('without_binding_plan',request,reg)
        messages,_=assemble(self.visible,reg,request)
        self.assertIn('没有目标框架绑定计划',messages[0]['content'])
        self.assertNotIn('bp',[x['role'] for x in json.loads(messages[1]['content'])['context']])
        with self.assertRaises(ValueError):admit_call('full',request,reg)
        with self.assertRaises(ValueError):admit_call('without_binding_plan',request,self.registry)

    def test_disabled_feedback_rejected_before_transport(self):
        client_calls=[]
        runner=CallRunner(Evidence(self.visible,self.registry),Path(self.tmp.name)/'compile-only',
                          client=lambda *a,**k:client_calls.append(1),variant='compile_only_repair')
        with self.assertRaises(ValueError):runner.call(self.requests['repair'])
        self.assertEqual(client_calls,[])

    def test_code_preflight_and_actual_target_update(self):
        artifact=self.responses['code_generate']['artifact'];request=self.requests['code_generate']
        result=apply_edits(self.visible,artifact,request)
        self.assertIn('target/example.c',result)
        self.assertIn('return 1', (self.visible/'target/example.c').read_text())
        with self.assertRaises(ContractError):apply_edits(self.visible,artifact,request)

    def test_invalid_second_edit_does_not_partially_change_first(self):
        artifact=deepcopy(self.responses['code_generate']['artifact']);request=deepcopy(self.requests['code_generate'])
        artifact['edits'].append(dict(path='target/new.c',operation='replace',base_sha256='0'*64,content='bad'))
        request['editable_paths'].append('target/new.c')
        before=(self.visible/'target/example.c').read_bytes()
        with self.assertRaises(ContractError):apply_edits(self.visible,artifact,request)
        self.assertEqual((self.visible/'target/example.c').read_bytes(),before)


class RevisionTests(unittest.TestCase):
    def test_parent_changes_invalidate_downstream_and_keep_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Revisions(tmp)
            fp=store.add('target_fp',{'kind':'fp','content':'v1'},{});dbc=store.add('dbc',{'kind':'dbc','content':'v1'},{'target_fp':fp.identity})
            bp=store.add('bp',{'kind':'bp','content':'v1'},{'dbc':dbc.identity,'target_fp':fp.identity})
            fp2=store.add('target_fp',{'kind':'fp','content':'v2'},{} )
            self.assertIn(dbc.identity,store.invalidated);self.assertIn(bp.identity,store.invalidated)
            self.assertTrue((Path(tmp)/(bp.identity+'.json')).exists())
            with self.assertRaises(ContractError):store.add('code',{'kind':'code','edits':[]},{'bp':bp.identity})

    def test_frozen_candidate_prohibits_any_feedback_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Revisions(tmp);revision=store.add('code',{'kind':'code','edits':[]},{})
            copy=revision.artifact;copy['edits'].append('malicious mutation')
            self.assertEqual(revision.artifact['edits'],[])
            frozen=store.freeze({'target/x.c':'0'*64});self.assertTrue(frozen)
            with self.assertRaises(ContractError):store.add('code',{'kind':'code','edits':['repair from score']},{})


class SolverTests(unittest.TestCase):
    def test_exact_domain_proof_and_counterexample(self):
        self.assertEqual(prove('x*2 >= x',{'x':'real'},['x >= 0'])['status'],'satisfied')
        result=prove('x*2 > x',{'x':'real'},['x >= 0'])
        self.assertEqual(result['status'],'violated');self.assertEqual(result['counterexample']['x'],'0')

    def test_unencoded_unknown_and_undefined_domain(self):
        self.assertEqual(prove('sqrt(x) >= 0',{'x':'real'},['x >= 0'])['status'],'unknown')
        self.assertEqual(prove('x/x == 1',{'x':'real'})['status'],'violated')
        self.assertEqual(prove('x/x == 1',{'x':'real'},['x != 0'])['status'],'satisfied')
        self.assertEqual(prove(True,{'x':'real'},['x > 0','x < 0'])['status'],'unknown')


class BindingTests(unittest.TestCase):
    def test_spi_frame_length_cannot_replace_total_header_and_read_clocks(self):
        dbc=parse("DeviceContract('synthetic',SPI(0,'selected'),FrozenPolicy(),{},Lifecycle(Sequence(),Transfer([WriteSegment([3]),ReadSegment(1,'f')],'end')),frames={'f':FrameLayout(1,{'raw':Field(0,8,'big')})})",'dbc')
        fp=parse("FrameworkProfile('synthetic','snapshot',capabilities={'send':APIContract('send',{},completion=Callback('done',{}))})",'fp')
        for length,state in [('FromDBCFrameLayout()','violated'),('FromDBCOperation()','satisfied')]:
            bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',primitive_bindings={'Transfer':BindAsyncCall(FromFP('capabilities.send'),FromFP('capabilities.send.completion'),length="+length+")})",'bp')
            report=check_binding(dbc,fp,bp,{},parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity({})})
            findings=[f for f in report['findings'] if f['check']=='spi_native_transaction_length']
            if state=='violated':self.assertEqual(findings[0]['status'],'violated')
            else:self.assertFalse(any(f['status']=='violated' for f in report['findings']))

    def test_completion_reference_and_current_instance_are_distinct_from_event_labels(self):
        dbc=parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),WriteBytes([1],name='x')))",'dbc')
        fp=parse("FrameworkProfile('synthetic','snapshot',capabilities={'send':APIContract('send',{},completion=Callback('done',{}))})",'fp')
        for completion,invalid in [("FromFP('capabilities.send.completion')",False),("EventRef('done','callback_success','x')",True)]:
            bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',primitive_bindings={'WriteBytes':BindAsyncCall(FromFP('capabilities.send'),"+completion+")})",'bp')
            report=check_binding(dbc,fp,bp,{},parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity({})})
            errors=[f for f in report['findings'] if f['status']=='violated']
            self.assertEqual(bool(errors),invalid)
            if invalid:self.assertTrue(any('FromFP' in f['detail'] for f in errors))

    def test_missing_binding_and_parent_drift_are_not_success(self):
        dbc=parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),WriteBytes([1],name='x',boundary='stop')))",'dbc')
        fp=parse("FrameworkProfile('synthetic','snapshot')",'fp')
        bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic')",'bp')
        result=check_binding(dbc,fp,bp,{},parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity({})})
        self.assertEqual(result['status'],'violated')
        self.assertTrue(any(f['check']=='action_coverage' for f in result['findings']))


if __name__=='__main__':unittest.main()
