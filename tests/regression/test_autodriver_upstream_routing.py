"""Typed prerequisite gaps route upstream without recycling candidate budgets."""
import json
from pathlib import Path
import tempfile
import unittest

import tests.regression.test_autodriver_candidate_loop as fixtures
from method.autodriver.implementation.system.loop import execute
from method.autodriver.implementation.system.contract.model import UpstreamGap


class UpstreamRoutingTests(unittest.TestCase):
    def diagnostic(self,proposal,root='dbc'):
        return {'schema':'autodriver.upstream-candidate-diagnostic/v1','producer':'method_trace_checker',
            'candidate_revision':proposal['revision'],'findings':[UpstreamGap(root,'quantities.x','executable_physical_relation','synthetic missing declaration').finding],
            'scope':'typed method checker prerequisite gaps; diagnostic text is not source evidence'}

    def fixture(self,root,variant='full'):
        helper=fixtures.CandidateLoopTests();p,calls=helper.pipeline(root,variant=variant)
        # The synthetic fixture's BP must really depend on its parents so that
        # the real revision store can invalidate BP/code on a DBC repair.
        p.revisions.add('bp',p.artifacts['bp'],{k:p.revisions.current[k] for k in ('dbc','target_fp')})
        def accept(key,artifact):
            response={'artifact':artifact,'call_id':'fixture.upstream.'+key,'evidence_links':[]}
            parents={} if key=='dbc' else {k:p.revisions.current[k] for k in ('dbc','target_fp')}
            return p.accept(key,response,{'semantic_validation':{'status':'satisfied','findings':[]}},
                [{'role':'task_context','material_id':'task'}],parents)
        repaired=[]
        def refine(key,diagnostic):
            repaired.append(key)
            return accept(key,{'kind':'dbc','format':'constructor','content':
                "DeviceContract('synthetic-repaired',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))"})
        old_bp=dict(p.artifacts['bp'])
        p.refine_contract=refine;p.bp=lambda:accept('bp',old_bp)
        # These are deliberately synthetic gate reports for a control-flow
        # unit test. Production admission has its own real positive/negative tests.
        p.assess_generation=lambda:{'status':'satisfied'}
        return p,calls,helper,repaired

    def test_parent_repair_invalidates_history_and_rebinds_code_without_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,calls,helper,repaired=self.fixture(Path(tmp));seen=[];initial={}
            def checker(workspace,proposal):
                seen.append(proposal['revision'])
                if len(seen)==1:
                    initial.update(p.revisions.current)
                    report=helper.observation(proposal,failed=False)
                    report['upstream_diagnostics']=[self.diagnostic(proposal)];return report
                return helper.observation(proposal,failed=len(seen)<5)
            result=execute(p,checker,development_partial=True)
            self.assertEqual(repaired,['dbc']);self.assertEqual(len(seen),5)
            self.assertEqual(result['code_repairs_used'],4)
            self.assertEqual([c['stage'] for c in calls],['code_generate']+['repair']*4)
            self.assertIn(initial['code'],p.revisions.invalidated);self.assertIn(initial['bp'],p.revisions.invalidated)
            self.assertNotEqual(initial['dbc'],p.revisions.current['dbc'])
            current=p.revisions.records[p.revisions.current['code']]
            self.assertEqual(current.parents['dbc'],p.revisions.current['dbc'])
            repair=p.code_proposal['request']
            for key in ('dbc','target_fp','bp'):
                self.assertIn({'role':key,'material_id':p.material_ids[key]},repair['selections'])
            self.assertEqual(p.upstream_repair_attempts,['dbc'])

    def test_failed_upstream_is_no_submission_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,calls,helper,_=self.fixture(Path(tmp));p.refine_contract=lambda *args:False
            def checker(workspace,proposal):
                report=helper.observation(proposal,failed=False);report['upstream_diagnostics']=[self.diagnostic(proposal)];return report
            result=execute(p,checker,development_partial=True)
            self.assertEqual(result['status'],'candidate_upstream_unresolved');self.assertIsNone(result['candidate'])
            self.assertEqual(len(calls),1)

    def test_exhausted_code_budget_preserves_fp_format_repair_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,calls,helper,_=self.fixture(Path(tmp))
            result=execute(p,lambda workspace,proposal:helper.observation(proposal),development_partial=True)
            self.assertEqual(result['code_repairs_used'],4)
            seen=[]
            def fp_client(prompt,**kwargs):
                data=json.loads(prompt);call=data['call'];seen.append(call)
                self.assertIn(data['expected_artifact_kind'],{'fp'})
                field="AsyncI2CContract('i','w','r',[],[],[],[],'unrecovered')" if len(seen)<3 else 'None'
                source=next(item for item in data['context'] if item['kind']=='framework_source')
                return json.dumps({'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':call['stage'],
                    'outcome':'candidate','artifact':{'kind':'fp','format':'constructor',
                        'content':"FrameworkProfile('nuttx',Ref("+repr(p.framework_scope['target'])+"),spi="+field+")"},
                    'evidence_links':[{'claim':'offline framework fixture','material_id':source['material_id'],
                        'start_line':source['start_line'],'end_line':source['start_line'],'basis':'synthetic only'}],
                    'unresolved':[],'context_requests':[],'change_summary':['offline fake response']})
            p.runner.client=fp_client
            self.assertTrue(p.fp('target'));self.assertEqual(len(seen),3)
            self.assertEqual(p.code_repairs_used(),4)

    def test_same_root_bound_and_feedback_interventions(self):
        for variant in ('full','compile_only_repair','without_dbc_evidence_refinement','without_binding_plan'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as tmp:
                p,calls,helper,repaired=self.fixture(Path(tmp),variant)
                p.propose_code(development_partial=True)
                if variant=='compile_only_repair':
                    with self.assertRaisesRegex(ValueError,'disabled'):p.repair_candidate_prerequisite([self.diagnostic(p.code_proposal)])
                elif variant=='without_dbc_evidence_refinement':
                    self.assertFalse(p.repair_candidate_prerequisite([self.diagnostic(p.code_proposal)]))
                elif variant=='without_binding_plan':
                    self.assertFalse(p.repair_candidate_prerequisite([self.diagnostic(p.code_proposal,'bp')]))
                else:
                    p.upstream_repair_attempts.append('dbc')
                    self.assertFalse(p.repair_candidate_prerequisite([self.diagnostic(p.code_proposal)]))
                self.assertEqual(repaired,[])
                wrong=self.diagnostic(p.code_proposal);wrong['candidate_revision']='0'*64
                if variant!='compile_only_repair':
                    with self.assertRaisesRegex(ValueError,'unapproved'):p.repair_candidate_prerequisite([wrong])
