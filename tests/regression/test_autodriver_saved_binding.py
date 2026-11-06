"""A same-parent cached BP still needs its composition checks and repair."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.test_autodriver_pipeline import prepared
from method.autodriver.implementation.system.pipeline import Pipeline


class SavedBindingTests(unittest.TestCase):
    def test_invalid_imported_binding_is_repaired_once_instead_of_re_admitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepared(root);seen=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt);call=data['call'];seen.append(call)
                self.assertEqual((call['stage'],call['repair_target']),('repair','bp'))
                report=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
                self.assertEqual(report['composition_checks']['status'],'violated')
                dbc=next(c['material_id'] for c in data['context'] if c['role']=='dbc');fp=next(c['material_id'] for c in data['context'] if c['role']=='target_fp')
                body=f"BindingPlan(Ref({dbc!r}),Ref({fp!r}),Ref('integration'),'synthetic',primitive_bindings={{'WriteBytes':BindAsyncCall(FromFP('capabilities.send'),FromFP('capabilities.send.completion'))}})"
                evidence=next(c for c in data['context'] if c['role']=='target_fp')
                return json.dumps({'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':call['stage'],'outcome':'candidate',
                    'artifact':{'kind':'bp','format':'constructor','content':body},'evidence_links':[{'claim':'synthetic','basis':'synthetic FP declaration',
                    'material_id':evidence['material_id'],'start_line':1,'end_line':1}],'unresolved':[],'context_requests':[],'change_summary':['synthetic BP repair']})
            p=Pipeline(root,client=client);p.select()
            values={'dbc':('dbc',"DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),WriteBytes([1])))"),
                    'target_fp':('fp',"FrameworkProfile('nuttx','synthetic',capabilities={'send':APIContract('send',{},completion=Callback('done',{}))})")}
            for key,(kind,text) in values.items():
                id='actual.'+key;artifact={'kind':kind,'format':'constructor','content':text}
                p.workspace.add_derived(id,key+'.constructor','fp_task_local' if kind=='fp' else 'dbc_candidate',key,text+'\n',['task'],os_role='target' if kind=='fp' else 'shared')
                p.artifacts[key]=artifact;p.material_ids[key]=id;p.revisions.add(key,artifact,{})
            integration=json.dumps(p.workspace.task['target']['interface'],ensure_ascii=False,indent=2)+'\n'
            p.workspace.add_derived('old.integration','old-integration.json','public_target_support','ancestor',integration,['task'],os_role='target')
            old="BindingPlan(Ref('actual.dbc'),Ref('actual.target_fp'),Ref('old.integration'),'synthetic',primitive_bindings={'WriteBytes':BindAsyncCall(FromFP('capabilities.send'),EventRef('done','callback_success','old'))})"
            p.workspace.add_derived('old.bp','old-bp.constructor','bp_candidate','candidate',old+'\n',['actual.dbc','actual.target_fp','old.integration'],os_role='target')
            p.saved_objects['bp']={'artifact':{'kind':'bp','format':'constructor','content':old},'material_id':'old.bp',
                'reviewed_task_sha256':p.workspace.entries['task']['sha256'],'parents':{'dbc':'actual.dbc','target_fp':'actual.target_fp'},
                'parent_materials':{'dbc':'actual.dbc','target_fp':'actual.target_fp'},'checks':{'status':'unknown','findings':[]},
                'origin':{'request_id':'synthetic'},'bundle_sha256':'0'*64}
            self.assertTrue(p.bp());self.assertEqual(len(seen),1);self.assertNotEqual(p.checks['binding']['status'],'violated')
            state=json.loads((p.output/'contract-state.json').read_text())
            self.assertEqual(state['checks']['binding'],p.checks['binding'])

    def test_local_aliases_recheck_semantics_and_preserve_declared_gaps(self):
        from method.autodriver.implementation.system.contract.check import check_contract,include_response_gaps
        from method.autodriver.implementation.system.contract.model import parse,identity
        from method.autodriver.implementation.system.artifacts import references_for
        for invalid in (False,True):
            with self.subTest(invalid=invalid),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);prepared(root);seen=[]
                def client(prompt,**kwargs):
                    data=json.loads(prompt);seen.append(data);call=data['call']
                    diagnostic=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
                    self.assertEqual(diagnostic['checks']['status'],'violated')
                    self.assertNotEqual(diagnostic['composition_checks']['status'],'violated')
                    dbc=next(c['material_id'] for c in data['context'] if c['role']=='dbc')
                    fp=next(c['material_id'] for c in data['context'] if c['role']=='target_fp')
                    text=f"BindingPlan(Ref({dbc!r}),Ref({fp!r}),Ref('integration'),'synthetic')"
                    return json.dumps({'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':call['stage'],'outcome':'candidate',
                        'artifact':{'kind':'bp','format':'constructor','content':text},'evidence_links':[{'claim':'synthetic parent',
                            'basis':'offline fixture only','material_id':fp,'start_line':1,'end_line':1}],
                        'unresolved':[],'context_requests':[],'change_summary':['synthetic semantic repair']})
                p=Pipeline(root,client=client);p.select()
                values={'dbc':('dbc',"DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))"),
                        'target_fp':('fp',"FrameworkProfile('nuttx','synthetic')")}
                for key,(kind,text) in values.items():
                    material='actual.'+key;artifact={'kind':kind,'format':'constructor','content':text}
                    p.workspace.add_derived(material,key+'.constructor','fp_task_local' if kind=='fp' else 'dbc_candidate',key,text+'\n',['task'],os_role='target' if kind=='fp' else 'shared')
                    p.artifacts[key]=artifact;p.material_ids[key]=material;p.revisions.add(key,artifact,{})
                integration=json.dumps(p.workspace.task['target']['interface'],ensure_ascii=False,indent=2)+'\n'
                p.workspace.add_derived('old.integration','old-integration.json','public_target_support','ancestor',integration,['task'],os_role='target')
                extra=",obligations={'x':Constraint('synthetic','invalid_origin',['claim'])}" if invalid else ''
                old="BindingPlan(Ref('actual.dbc'),Ref('actual.target_fp'),Ref('old.integration'),'synthetic'"+extra+")"
                p.workspace.add_derived('old.bp','old-bp.constructor','bp_candidate','candidate',old+'\n',['actual.dbc','actual.target_fp','old.integration'],os_role='target')
                _,old_checks=check_contract(old,'bp',references=references_for(p.workspace))
                old_checks=include_response_gaps(old_checks,[{'subject':'provider','reason':'synthetic unresolved provider','impact':'required'}])
                p.saved_objects['bp']={'artifact':{'kind':'bp','format':'constructor','content':old},'material_id':'old.bp',
                    'reviewed_task_sha256':p.workspace.entries['task']['sha256'],'parents':{'dbc':'actual.dbc','target_fp':'actual.target_fp'},
                    'parent_materials':{'dbc':'actual.dbc','target_fp':'actual.target_fp'},'checks':old_checks,
                    'evidence_links':[],'unresolved':[{'subject':'provider','reason':'synthetic unresolved provider','impact':'required'}],
                    'origin':{'request_id':'synthetic'},'bundle_sha256':'0'*64}
                self.assertTrue(p.bp());self.assertEqual(len(seen),int(invalid))
                self.assertEqual(p.checks['bp']['contract_sha256'],identity(parse(p.artifacts['bp']['content'],'bp')))
                self.assertEqual(p.workspace.evidence().read('old.bp').decode(),old+'\n')
                if not invalid:
                    self.assertEqual(p.checks['bp']['status'],'unknown')
                    self.assertTrue(any(f['check']=='declared_response_gap' for f in p.checks['bp']['findings']))
                    self.assertNotEqual(p.checks['bp']['contract_sha256'],old_checks['contract_sha256'])
