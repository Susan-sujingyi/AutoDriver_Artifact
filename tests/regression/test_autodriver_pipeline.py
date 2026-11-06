"""Prepared synthetic SD input through production stages, using a fake client."""
import json
from pathlib import Path
import tempfile
import unittest
import shutil
from unittest.mock import patch

import yaml

from method.autodriver.implementation.system.pipeline import Pipeline
from method.autodriver.implementation.system.protocol.assembler import digest,canonical


def prepared(root):
    import pymupdf
    task={'schema':'autodriver.public-task/v1','task_id':'synthetic-sd','sensor':'synthetic','revision':1,'condition':'SD',
          'requirements':{'connection':{'i2c':{'address_7bit':17}},'functions':{'channels':['temperature']}},
          'source':{'os':'zephyr','selected_files':[{'path':'source_os/device/input.c','role':'primary_source'}],
                    'configuration_files':['source_inputs/configuration.yaml']},
          'target':{'os':'nuttx','board':'synthetic','interface':{'register':'int synthetic_register(void);'},
                    'interface_files':['target/include/nuttx/sensors/synthetic.h']},
          'submission':{'editable_paths':['drivers/sensors/synthetic.c']}}
    files={'task.yaml':yaml.safe_dump(task),'source_os/device/input.c':'int acquire(void) {return transfer();}\n',
           'source_inputs/configuration.yaml':'CONFIG_GIVEN: true\n','target/include/nuttx/sensors/synthetic.h':'int synthetic_register(void);\n',
           'source_os/include/zephyr/drivers/i2c.h':'int transfer(void);\n',
           'source_os/include/zephyr/drivers/sensor.h':'int sample(void);\n','source_os/include/zephyr/kernel.h':'int sleep(void);\n',
           'target/include/nuttx/i2c/i2c_master.h':'int native_transfer(void);\n',
           'target/include/nuttx/sensors/sensor.h':'int sensor_register(void);\n',
           'target/include/nuttx/clock.h':'int now(void);\n','target/include/nuttx/signal.h':'int delay(void);\n'}
    for path,text in files.items():
        p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    pdf=pymupdf.open();page=pdf.new_page();page.insert_text((72,72),'Synthetic device. One transfer yields an unsigned byte.');path=root/'documents/raw.pdf';path.parent.mkdir();pdf.save(path);pdf.close()
    inventory={str(p.relative_to(root)):{'sha256':digest(p.read_bytes()),'executable':False} for p in root.rglob('*') if p.is_file()}
    (root/'materials.json').write_text(json.dumps({'schema':'autodriver.materials/v1','task_id':'synthetic-sd','condition':'SD','files':inventory,'identity':digest(canonical(inventory).encode())}))
    (root/'output').mkdir()


class PipelineTests(unittest.TestCase):
    def test_codegen_uses_complete_parents_and_retrieves_framework_declarations_on_demand(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared(root);seen=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data);call=data['call']
                evidence=next(c for c in data['context'] if c['role']=='dbc')
                response={'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':call['stage'],
                    'outcome':'candidate','artifact':{'kind':'code','format':'file_edits','edits':[{
                        'path':'target/drivers/sensors/synthetic.c','operation':'create','base_sha256':None,
                        'content':'int synthetic_register(void) { return 0; }\n'}]},
                    'evidence_links':[{'claim':'synthetic only','material_id':evidence['material_id'],
                        'start_line':1,'end_line':1,'basis':'synthetic contract'}],
                    'unresolved':[],'context_requests':[],'change_summary':['offline context boundary fixture']}
                if len(seen)==1:
                    self.assertEqual(call['stage'],'code_generate')
                    self.assertFalse(any(c['role']=='framework_evidence' for c in data['context']))
                    self.assertTrue({'dbc','target_fp','target_support','validation_report'}<=set(c['role'] for c in data['context']))
                    self.assertIn('target_framework',data['allowed_context_request_scopes'])
                    self.assertIn('query_framework',data['available_tool_names'])
                    response.update(outcome='needs_evidence',artifact=None,context_requests=[{'scope':'internal_tools',
                        'question':'Read the complete approved native declaration','reason':'Need its actual signature',
                        'query':json.dumps({'tool':'query_framework','arguments':{'scope':'target_framework','query':'native_transfer'}})}])
                else:
                    self.assertEqual(call['stage'],'repair');self.assertEqual(call['repair_target'],'code')
                    retrieved=[c for c in data['context'] if c['role']=='framework_evidence']
                    self.assertTrue(any('int native_transfer(void);' in c['text'] for c in retrieved))
                return json.dumps(response)
            p=Pipeline(root,client=client,variant_name='without_binding_plan');p.select()
            for key,kind,text in [('target_fp','fp',f"FrameworkProfile('nuttx',Ref({p.framework_scope['target']!r}))"),
                    ('dbc','dbc',"DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))")]:
                id='parent.'+key;artifact={'kind':kind,'format':'constructor','content':text}
                p.workspace.add_derived(id,key+'.constructor','fp_task_local' if kind=='fp' else 'dbc_candidate',key,text+'\n',['task'],os_role='target' if kind=='fp' else 'shared')
                p.material_ids[key]=id;p.artifacts[key]=artifact;p.checks[key]={'status':'unknown','findings':[]};p.revisions.add(key,artifact,{})
            p.update_evidence();result=p.propose_code(development_partial=True)
            self.assertEqual(result['status'],'development_partial_candidate_proposed');self.assertEqual(len(seen),2)
            self.assertEqual(p.code_repairs_used(),1)

    def run_pipeline(self,root,variant,*,blocked_dbc=False):
        calls=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt);call=data['call'];stage=call['stage'];kind=data['expected_artifact_kind'];calls.append(data)
            contexts=data['context']
            if kind=='fp':
                scope=next(c for c in contexts if c['role']=='framework_context');os_name=json.loads(scope['text'])['os']
                content=f"FrameworkProfile({os_name!r},Ref({scope['material_id']!r}))"
            elif kind=='dbc':
                content="DeviceContract('synthetic',I2C(17),FrozenPolicy(mode='given'),{},Lifecycle(Sequence(),WriteBytes([1],name='send',boundary='stop'),on_failure=FailCurrentOperation(True)))"
            else:
                dbc=next(c['material_id'] for c in contexts if c['role']=='dbc');fp=next(c['material_id'] for c in contexts if c['role']=='target_fp')
                content=f"BindingPlan(Ref({dbc!r}),Ref({fp!r}),Ref('integration'),'synthetic')"
            evidence=next(c for c in contexts if c['role'] not in {'method_specification','candidate','internal_diagnostic'})
            response={'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':stage,'outcome':'candidate',
                      'artifact':{'kind':kind,'format':'constructor','content':content},'evidence_links':[
                          {'claim':'synthetic only','material_id':evidence['material_id'],'start_line':1,'end_line':1,'basis':'synthetic protocol input'}],
                      'unresolved':[],'context_requests':[],'change_summary':['synthetic production orchestration test; no real model']}
            if kind=='dbc' and blocked_dbc:response.update(outcome='blocked',artifact=None,
                unresolved=[{'subject':'synthetic.device','reason':'offline fixture has no sufficient device facts','impact':'required'}])
            return json.dumps(response)
        pipeline=Pipeline(root,client=client,variant_name=variant)
        result=pipeline.run()
        return pipeline,result,calls

    def test_failed_dbc_never_starts_unrelated_target_fp_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared(root);pipeline,result,calls=self.run_pipeline(root,'full',blocked_dbc=True)
            self.assertEqual([item['call']['stage'] for item in calls],['fp_build','dbc_recover'])
            self.assertEqual(result['stage'],'dbc');self.assertEqual(result['status'],'upstream_no_candidate')
            self.assertNotIn('target_fp',pipeline.artifacts)

    def test_full_stages_do_not_open_generation_on_unknown_or_violated_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared(root);pipeline,result,calls=self.run_pipeline(root,'full')
            self.assertEqual([c['call']['stage'] for c in calls[:5]],['fp_build','dbc_recover','repair','fp_build','bp_build'])
            self.assertEqual([c['call'].get('repair_target') for c in calls[5:7]],['bp','bp'])
            history=json.loads((pipeline.output/'upstream-admission-history.json').read_text())
            self.assertEqual(history['attempted_objects'],['dbc','target_fp','bp'])
            self.assertEqual(history['maximum_semantic_repairs'],3)
            self.assertEqual(len(history['reports']),4)
            fp_repair=next(c for c in calls if c['call']['stage']=='repair' and c['call'].get('repair_target')=='fp')
            diagnostic=next(json.loads(c['text']) for c in fp_repair['context'] if c['role']=='internal_diagnostic')
            self.assertEqual(set(diagnostic['checks']),{'framework_checks','support_audit'})
            self.assertFalse(any(c['kind'] in {'device_source','document_extract','dbc_candidate'} for c in fp_repair['context']))
            bp=pipeline.revisions.records[pipeline.revisions.current['bp']]
            self.assertEqual(bp.parents,{name:pipeline.revisions.current[name] for name in ('dbc','target_fp')})
            self.assertTrue(pipeline.revisions.invalidated)
            self.assertEqual(result['status'],'upstream_obligations_violated');self.assertIsNone(result['candidate'])
            self.assertNotIn('code_generate',[c['call']['stage'] for c in calls])
            self.assertIn('source.syntax',pipeline.workspace.entries)
            task_projection=json.loads(pipeline.workspace.evidence().read('task'))
            integration=task_projection['native_interface']['public_integration_evidence'][0]
            self.assertEqual(integration['text'],(root/'target/include/nuttx/sensors/synthetic.h').read_text())
            self.assertEqual(pipeline.workspace.entries['task']['parents'],['support.0'])
            # Frozen original files remain unchanged, including raw source.
            for path,entry in pipeline.workspace.manifest['files'].items():self.assertEqual(digest((root/path).read_bytes()),entry['sha256'])

    def test_no_source_fp_keeps_exact_same_raw_source_framework(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            first=Path(a);second=Path(b);prepared(first);prepared(second)
            full,_,_=self.run_pipeline(first,'full');ablated,_,calls=self.run_pipeline(second,'without_source_framework_lifting')
            self.assertNotIn('source_fp',ablated.artifacts)
            self.assertEqual({full.workspace.entries[id]['sha256'] for id in full.framework_ids['source']},
                             {ablated.workspace.entries[id]['sha256'] for id in ablated.framework_ids['source']})
            dbc=next(c for c in calls if c['call']['stage']=='dbc_recover')
            self.assertFalse(any(c['role']=='source_fp' for c in dbc['context']))

    def test_refinement_and_bp_interventions_skip_real_stages(self):
        for variant,absent in [('without_dbc_evidence_refinement','dbc'),('without_binding_plan','bp')]:
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);prepared(root);_,_,calls=self.run_pipeline(root,variant)
                self.assertFalse(any(c['call']['stage']=='repair' and c['call'].get('repair_target')==absent for c in calls))
                if absent=='bp':self.assertNotIn('bp_build',[c['call']['stage'] for c in calls])

    def test_s_and_d_rejected_before_any_transport(self):
        for condition in ('S','D'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);prepared(root);path=root/'task.yaml';task=yaml.safe_load(path.read_text());task['condition']=condition;path.write_text(yaml.safe_dump(task))
                manifest=json.loads((root/'materials.json').read_text());manifest['files']['task.yaml']['sha256']=digest(path.read_bytes());(root/'materials.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):Pipeline(root,client=lambda *a,**k:self.fail('unexpected model call'))

    def test_same_input_fp_checkpoint_is_rechecked_without_new_fp_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'original';root.mkdir();prepared(root)
            original,_,_=self.run_pipeline(root,'full')
            other=Path(tmp)/'continued';shutil.copytree(root,other);shutil.rmtree(other/'output');(other/'output').mkdir()
            profiles={}
            for role in ('source','target'):
                response=json.loads((root/('output/autodriver/'+role+'_fp-response.json')).read_text());id=response['call_id']
                prefix=root/'output/requests'/id
                profiles[role]=dict(response=response,response_sha256=digest(canonical(response).encode()),
                    request=json.loads(Path(str(prefix)+'.request.json').read_text()),
                    messages=json.loads(Path(str(prefix)+'.messages.json').read_text()),
                    origin_request_id='synthetic-'+role,host_receipt_sha256='f'*64)
            checkpoint=dict(task_id=original.workspace.task['task_id'],input_identity=original.workspace.manifest['identity'],profiles=profiles)
            cp=other/'output/fp-checkpoint.json';cp.write_text(json.dumps(checkpoint))
            with patch.dict('os.environ',{'AUTODRIVER_FP_CHECKPOINT':str(cp),'AUTODRIVER_FP_CHECKPOINT_SHA256':digest(cp.read_bytes())}):
                continued=Pipeline(other,client=lambda *a,**k:self.fail('cached FP must not be sent again'))
                continued.select();self.assertTrue(continued.fp('source'));self.assertTrue(continued.fp('target'))
                self.assertEqual(len(continued.runner.calls),2)
                self.assertTrue(all(c['new_upstream_requests']==0 for c in continued.runner.calls))
                self.assertEqual(continued.artifacts,{'source_fp':original.artifacts['source_fp'],'target_fp':original.artifacts['target_fp']})
            # An old target FP can instead seed a new evidence-scoped build;
            # it is never silently treated as a guarantee for that scope.
            seeded=Path(tmp)/'seeded';shutil.copytree(root,seeded);shutil.rmtree(seeded/'output');(seeded/'output').mkdir()
            checkpoint['profiles']={'source':profiles['source']};checkpoint['seeds']={'target':profiles['target']}
            seed_path=seeded/'output/fp-checkpoint.json';seed_path.write_text(json.dumps(checkpoint));transport=[]
            def build_seed(prompt,**kwargs):
                data=json.loads(prompt);transport.append(data)
                self.assertTrue(any(c['role']=='existing_fp' for c in data['context']))
                self.assertIn('HoldsBufferUntilCompletion',data['call']['goal'])
                response=json.loads(json.dumps(profiles['target']['response']))
                response.update(call_id=data['call']['call_id'],stage=data['call']['stage']);return json.dumps(response)
            with patch.dict('os.environ',{'AUTODRIVER_FP_CHECKPOINT':str(seed_path),'AUTODRIVER_FP_CHECKPOINT_SHA256':digest(seed_path.read_bytes())}):
                p=Pipeline(seeded,client=build_seed);p.select();self.assertTrue(p.fp('source'));self.assertTrue(p.fp('target'))
                self.assertEqual(len(transport),1);self.assertEqual(transport[0]['call']['stage'],'fp_build')
            # Host pin protects local checkpoint payloads from later changes.
            cp.write_text('{}')
            shutil.rmtree(other/'output/autodriver')
            with patch.dict('os.environ',{'AUTODRIVER_FP_CHECKPOINT':str(cp),'AUTODRIVER_FP_CHECKPOINT_SHA256':'0'*64}):
                with self.assertRaisesRegex(ValueError,'frozen material changed'):Pipeline(other)

    def test_partial_proposal_is_explicit_and_does_not_apply_candidate_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared(root)
            def client(prompt,**kwargs):
                data=json.loads(prompt);call=data['call'];kind=data['expected_artifact_kind']
                if kind=='fp':
                    scope=next(c for c in data['context'] if c['role']=='framework_context');name=json.loads(scope['text'])['os']
                    artifact={'kind':'fp','format':'constructor','content':f"FrameworkProfile({name!r},Ref({scope['material_id']!r}))"}
                elif kind=='dbc':artifact={'kind':'dbc','format':'constructor','content':"DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),WriteBytes([1],name='write',boundary='stop'),on_failure=FailCurrentOperation(True)))"}
                else:
                    self.assertEqual(call['generation_mode'],'without_binding')
                    self.assertIn('development partial',call['generation_scope'])
                    report=json.loads(next(c for c in data['context'] if c['role']=='validation_report')['text'])
                    self.assertEqual(report['full_scope_gate'],'closed')
                    self.assertEqual(report['admission_location'],'checks.generation_admission')
                    self.assertEqual(report['checks'],p.checks)
                    self.assertNotIn('admission',report)
                    artifact={'kind':'code','format':'file_edits','edits':[{'path':'target/drivers/sensors/synthetic.c','operation':'create','base_sha256':None,'content':'int synthetic(void) {return 1;}\n'}]}
                evidence=next(c for c in data['context'] if c['role'] not in {'method_specification','candidate','internal_diagnostic'})
                return json.dumps({'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':call['stage'],'outcome':'candidate','artifact':artifact,
                    'evidence_links':[{'claim':'synthetic only','material_id':evidence['material_id'],'start_line':evidence['start_line'],'end_line':evidence['start_line'],'basis':'synthetic input'}],
                    'unresolved':[],'context_requests':[],'change_summary':['fake transport only']})
            p=Pipeline(root,client=client,variant_name='without_binding_plan');result=p.run(stop_after='prototype')
            self.assertEqual(result['status'],'development_partial_candidate_proposed')
            self.assertFalse(result['code_proposal']['applied']);self.assertIsNone(result['candidate'])
            self.assertFalse((root/'target/drivers/sensors/synthetic.c').exists())

    def test_code_repair_uses_only_pinned_parents_and_cumulative_baseline(self):
        for changed in (False,True,'aliases','opaque'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);prepared(root);seen=[]
                code={'kind':'code','format':'file_edits','edits':[{'path':'target/drivers/sensors/synthetic.c','operation':'create','base_sha256':None,'content':'int synthetic(void) {return 2;}\n'}]}
                def client(prompt,**kwargs):
                    data=json.loads(prompt);call=data['call'];seen.append(call)
                    self.assertEqual(call['stage'],'repair');self.assertEqual(call['repair_target'],'code')
                    self.assertEqual(call['base_files'],{})
                    candidates=[c for c in data['context'] if c['role']=='candidate']
                    if changed=='opaque':self.assertEqual(candidates[0]['text'],'opaque invalid output\n')
                    else:self.assertEqual(json.loads(candidates[0]['text'])['kind'],'code')
                    evidence=next(c for c in data['context'] if c['role']=='dbc')
                    return json.dumps({'schema':'autodriver.llm-response/v0.1','call_id':call['call_id'],'stage':'repair','outcome':'candidate','artifact':code,
                        'evidence_links':[{'claim':'synthetic only','material_id':evidence['material_id'],'start_line':1,'end_line':1,'basis':'synthetic contract'}],
                        'unresolved':[],'context_requests':[],'change_summary':['fake cumulative code repair']})
                p=Pipeline(root,client=client);p.select()
                data={'target_fp':('fp',f"FrameworkProfile('nuttx',Ref({p.framework_scope['target']!r}))"),
                      'dbc':('dbc',"DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))"),
                      'bp':('bp',"BindingPlan(Ref('parent.dbc'),Ref('parent.target_fp'),Ref('support.0'),'synthetic')")}
                for key,(kind,text) in data.items():
                    id='parent.'+key;artifact={'kind':kind,'format':'constructor','content':text}
                    p.workspace.add_derived(id,key+'.constructor',kind+'_candidate' if kind!='fp' else 'fp_task_local',key,text+'\n',['task'],os_role='target' if kind!='dbc' else 'shared')
                    p.material_ids[key]=id;p.artifacts[key]=artifact;p.checks[key]={'status':'unknown','findings':[]}
                    p.revisions.add(key,artifact,{})
                p.checks['binding']={'status':'unknown','findings':[]};p.update_evidence()
                pins={key:p.workspace.entries[id]['sha256'] for key,id in p.material_ids.items()}
                if changed in (True,'aliases'):pins['dbc']='0'*64
                p.code_seed={'artifact':code,'parents':pins,'origin':{'request_id':'synthetic'},'repair_depth':1,'base_files':{},
                    'editable_paths':['target/'+v for v in p.workspace.task['submission']['editable_paths']],
                    'diagnostics':[{'producer':'method_trace_checker','reason':'synthetic counterexample'}]}
                if changed=='aliases':
                    from method.autodriver.implementation.system.artifacts import semantic_identity
                    p.code_seed['parent_semantics']={'dbc':semantic_identity(p.workspace,p.artifacts['dbc']['content'],'dbc')}
                if changed=='opaque':p.code_seed.update(artifact=None,rejected_response_text='opaque invalid output')
                if changed is True:
                    with self.assertRaisesRegex(ValueError,'code repair parent changed'):p.propose_code(development_partial=True)
                    self.assertEqual(seen,[])
                else:
                    result=p.propose_code(development_partial=True)
                    self.assertEqual(result['status'],'development_partial_candidate_proposed');self.assertEqual(len(seen),1)
                    self.assertFalse((root/'target/drivers/sensors/synthetic.c').exists())


if __name__=='__main__':unittest.main()
