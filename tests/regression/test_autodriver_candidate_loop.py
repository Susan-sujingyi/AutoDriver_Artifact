"""Fake model/public-tool fixtures through actual cumulative code repair."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.test_autodriver_pipeline import prepared
from method.autodriver.implementation.system.pipeline import Pipeline
from method.autodriver.implementation.system.loop import execute


class CandidateLoopTests(unittest.TestCase):
    def test_pretransport_repair_context_limit_is_retained_as_its_actual_stop(self):
        from unittest.mock import patch
        from method.autodriver.implementation.system.protocol.assembler import ContextLimitError
        with tempfile.TemporaryDirectory() as temp:
            p,calls=self.pipeline(Path(temp))
            with patch.object(p,'repair_code',side_effect=ContextLimitError(600000,524288)):
                result=execute(p,lambda workspace,proposal:self.observation(proposal),development_partial=True)
            self.assertEqual(result['status'],'candidate_repair_response_rejected')
            self.assertEqual(result['repair_stop'],{'error_type':'ContextLimitError',
                'reason':'context exceeds budget; no silent truncation is permitted',
                'target':'code','kind':'context_bytes','limit':524288,'used':600000})
            self.assertIsNone(result['candidate'])
            self.assertEqual(len(calls),1)

    def pipeline(self,root,*,bad_initial=False,variant='full',ask_on_last=False):
        prepared(root);calls=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt);call=data['call'];calls.append(call)
            self.assertEqual(data['expected_artifact_kind'],'code')
            evidence=next(c for c in data['context'] if c['role']=='dbc')
            artifact={'kind':'code','format':'file_edits','edits':[{'path':'target/drivers/sensors/synthetic.c','operation':'create',
                'base_sha256':None,'content':'int value(void) {return '+str(len(calls))+';}\n'}]}
            response={'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':call['stage'],'outcome':'candidate','artifact':artifact,
                'evidence_links':[{'claim':'synthetic fixture','material_id':evidence['material_id'],'start_line':1,
                    'end_line':99999 if bad_initial and len(calls)==1 else 1,'basis':'synthetic contract'}],
                'unresolved':[],'context_requests':[],'change_summary':['offline fake response']}
            if ask_on_last and sum(c['stage']=='repair' for c in calls)==4:
                response.update(outcome='needs_evidence',artifact=None,context_requests=[{'scope':'internal_tools','question':'synthetic final-slot question',
                    'query':'{"tool":"unavailable","arguments":{}}','reason':'test that evidence continuations cannot reset the four-request limit'}])
            return json.dumps(response)
        p=Pipeline(root,client=client,variant_name=variant);p.select()
        values={'target_fp':('fp',f"FrameworkProfile('nuttx',Ref({p.framework_scope['target']!r}))"),
            'dbc':('dbc',"DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))"),
            'bp':('bp',"BindingPlan(Ref('parent.dbc'),Ref('parent.target_fp'),Ref('support.0'),'synthetic')")}
        for key,(kind,text) in values.items():
            id='parent.'+key;artifact={'kind':kind,'format':'constructor','content':text}
            p.workspace.add_derived(id,key+'.constructor','fp_task_local' if kind=='fp' else kind+'_candidate',key,text+'\n',['task'],os_role='target' if kind!='dbc' else 'shared')
            p.material_ids[key]=id;p.artifacts[key]=artifact;p.checks[key]={'status':'unknown','findings':[]};p.revisions.add(key,artifact,{})
        p.checks['binding']={'status':'unknown','findings':[]};p.update_evidence()
        # Explicit synthetic partial-development fixture; never normal admission.
        p.run=lambda **kwargs:p.propose_code(development_partial=True)
        return p,calls

    def observation(self,proposal,*,failed=True,producer='approved_compile_tool'):
        return {'schema':'autodriver.candidate-check/v1','revision':proposal['revision'],'status':'completed',
            'compile_status':'fail' if failed else 'pass','native_checks_status':'unknown','independent_scoring':'not_run','hardware_status':'not_run',
            'repair_diagnostics':[{'producer':producer,'reason':'synthetic public-tool counterexample'}] if failed else []}

    def test_four_repairs_share_baseline_and_do_not_inherit_success(self):
        with tempfile.TemporaryDirectory() as temp:
            p,calls=self.pipeline(Path(temp));seen=[];parents=dict(p.revisions.current)
            def checker(workspace,proposal):
                seen.append(proposal)
                return self.observation(proposal,failed=len(seen)<5)
            result=execute(p,checker,development_partial=True)
            self.assertEqual(result['status'],'candidate_checks_unknown');self.assertEqual(result['code_repairs_used'],4)
            self.assertEqual(len(seen),5);self.assertEqual([c['stage'] for c in calls],['code_generate']+['repair']*4)
            self.assertTrue(all(c['base_files']=={} for c in calls))
            self.assertTrue(all(item['artifact']['edits'][0]['operation']=='create' for item in seen))
            self.assertTrue(all(p.revisions.current[k]==v for k,v in parents.items()))
            self.assertFalse((Path(temp)/'target/drivers/sensors/synthetic.c').exists())

    def test_format_repair_consumes_same_cap_and_compile_only_rejects_native_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            p,calls=self.pipeline(Path(temp),bad_initial=True);seen=[]
            def checker(workspace,proposal):seen.append(proposal);return self.observation(proposal)
            result=execute(p,checker,development_partial=True)
            self.assertEqual(result['status'],'candidate_repair_bound_exhausted')
            self.assertEqual(result['code_repairs_used'],4);self.assertEqual(len(seen),4)
            self.assertEqual(result['final_candidate_disposition'],'last_applied_candidate')
            self.assertEqual(sum(c['stage']=='repair' for c in calls),4)
        with tempfile.TemporaryDirectory() as temp:
            p,calls=self.pipeline(Path(temp),variant='compile_only_repair')
            with self.assertRaisesRegex(ValueError,'unapproved candidate repair diagnostic'):
                execute(p,lambda workspace,proposal:self.observation(proposal,producer='method_trace_checker'),development_partial=True)
            self.assertEqual(len(calls),1)

    def test_evidence_request_on_last_code_repair_cannot_start_a_fifth(self):
        with tempfile.TemporaryDirectory() as temp:
            p,calls=self.pipeline(Path(temp),ask_on_last=True)
            result=execute(p,lambda workspace,proposal:self.observation(proposal),development_partial=True)
            self.assertEqual(result['status'],'candidate_repair_response_rejected')
            self.assertIsNone(result['candidate']);self.assertEqual(result['final_candidate_disposition'],'no_submission_after_latest_repair')
            self.assertEqual(result['code_repairs_used'],4)
            self.assertEqual(sum(c['stage']=='repair' for c in calls),4)
