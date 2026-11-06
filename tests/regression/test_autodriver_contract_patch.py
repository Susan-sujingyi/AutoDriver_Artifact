"""Single-object field repairs preserve unedited mathematical/source values."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.calls import CallRunner
from method.autodriver.implementation.system.contract.model import parse,ContractError
from method.autodriver.implementation.system.contract_patch import tree_from_text
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.protocol.assembler import assemble,check_response,digest
from method.autodriver.implementation.system.syntax import normalize_contract_response
from method.autodriver.implementation.system.transport import render_tree


class ContractPatchTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.visible,self.registry,self.requests,self.responses=make_fixture(temporary.name)

    def repair(self,kind='dbc'):
        stage={'dbc':'dbc_recover','fp':'fp_build','bp':'bp_build'}[kind]
        request=deepcopy(self.requests[stage]);request.update(stage='repair',repair_target=kind,call_id='field-repair')
        candidate='target_fp' if kind=='fp' else kind
        request['selections'] += [{'role':'candidate','material_id':candidate},{'role':'internal_diagnostic','material_id':'diagnostic'}]
        next(e for e in self.registry['materials'] if e['id']=='diagnostic')['parents']=[candidate]
        messages,_=assemble(self.visible,self.registry,request)
        base=json.loads(messages[-1]['content'])['contract_repair_base']
        response=deepcopy(self.responses[stage]);response.update(stage='repair',call_id=request['call_id'])
        response['artifact']={'kind':kind,'format':'constructor_edits','base_material_id':base['material_id'],'base_sha256':base['sha256'],
            'edits':[{'operation':'replace','path':['unresolved'],'value':['original unresolved facts retained','new explicit gap']}]}
        return request,messages,response

    def test_all_three_contract_layers_preserve_unedited_fields_and_checks(self):
        for kind in ('fp','dbc','bp'):
            with self.subTest(kind=kind):
                request,messages,response=self.repair(kind);normalized,audit=normalize_contract_response(response,messages=messages)
                base=next(c for c in json.loads(messages[-1]['content'])['context'] if c['role']=='candidate')
                old=parse(base['text'],kind);new=parse(normalized['artifact']['content'],kind)
                self.assertEqual({k:v for k,v in old.fields.items() if k!='unresolved'},{k:v for k,v in new.fields.items() if k!='unresolved'})
                self.assertEqual(audit['base_sha256'],base['sha256']);self.assertEqual(audit['edits'][0]['path'],['unresolved'])
                self.assertEqual(check_response(response,request,messages)['semantic_validation']['status'],'unknown')

    def test_wrong_base_overlap_reserved_keys_and_nonexistent_paths_are_refused(self):
        _,messages,response=self.repair()
        corruptions=[]
        for key,value in [('base_sha256','0'*64),('base_material_id','target_fp'),('kind','fp')]:
            r=deepcopy(response);r['artifact'][key]=value;corruptions.append(r)
        for edits in ([{'operation':'replace','path':[],'value':None}],
                      [{'operation':'replace','path':['$constructor'],'value':'FrameworkProfile'}],
                      [{'operation':'remove','path':['unresolved'],'value':None}],
                      [{'operation':'replace','path':['missing'],'value':None}],
                      [{'operation':'add','path':['unresolved'],'value':None}],
                      [{'operation':'replace','path':['unresolved',99],'value':'x'}],
                      [{'operation':'replace','path':['unresolved'],'value':[]},{'operation':'replace','path':['unresolved',0],'value':'x'}]):
            r=deepcopy(response);r['artifact']['edits']=edits;corruptions.append(r)
        for corrupt in corruptions:
            with self.subTest(artifact=corrupt['artifact']),self.assertRaises(ContractError):normalize_contract_response(corrupt,messages=messages)
        with self.assertRaises(ContractError):normalize_contract_response(response)

    def test_exact_numeric_literals_and_nested_varargs_roundtrip(self):
        text="DeviceContract('synthetic',I2C(7),FrozenPolicy(precise=-0.000000000000000000125,large=1208925819614629174706187),{},Lifecycle(Sequence(WriteBytes([1]),WriteBytes([2])),Sequence()),quantities={'p':PhysicalQuantity('a/3','Pa',domain={'a':{'type':'int','bounds':(-5,9)}})})"
        self.assertEqual(parse(render_tree(tree_from_text(text,'dbc'),'dbc'),'dbc'),parse(text,'dbc'))

    def test_append_preserves_existing_list_indices_and_cannot_create_or_overlap_paths(self):
        request,messages,response=self.repair()
        response['artifact']['edits']=[{'operation':'append','path':['unresolved'],'value':'new gap'}]
        normalized,audit=normalize_contract_response(response,messages=messages)
        original=next(c['text'] for c in json.loads(messages[-1]['content'])['context'] if c['role']=='candidate')
        self.assertEqual(parse(normalized['artifact']['content'],'dbc')['unresolved'],parse(original,'dbc')['unresolved']+('new gap',))
        for path in (['missing'],['connection'],['unresolved',0]):
            response['artifact']['edits'][0]['path']=path
            with self.subTest(path=path),self.assertRaises(ContractError):normalize_contract_response(response,messages=messages)

    def test_ordered_appends_and_existing_index_updates_have_distinct_writes(self):
        request,messages,response=self.repair()
        response['artifact']['edits']=[{'operation':'append','path':['unresolved'],'value':'first appended gap'},
            {'operation':'replace','path':['unresolved',0],'value':'revised existing gap'},
            {'operation':'append','path':['unresolved'],'value':'second appended gap'}]
        result,_=normalize_contract_response(response,messages=messages)
        self.assertEqual(parse(result['artifact']['content'],'dbc')['unresolved'],('revised existing gap','first appended gap','second appended gap'))
        response['artifact']['edits'].append({'operation':'replace','path':['unresolved'],'value':[]})
        with self.assertRaises(ContractError):normalize_contract_response(response,messages=messages)
        # New appended slots cannot be used as a guessed base for more edits.
        response['artifact']['edits']=[{'operation':'append','path':['unresolved'],'value':'new'},
            {'operation':'replace','path':['unresolved',1],'value':'guess'}]
        with self.assertRaises(ContractError):normalize_contract_response(response,messages=messages)

    def test_real_runner_repairs_only_the_bad_expression_without_rewriting_sibling(self):
        content="DeviceContract('synthetic',I2C(7),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence(),recovery=None,reentry=None),quantities={'bad':PhysicalQuantity('raw +','Pa'),'keep':PhysicalQuantity('(raw*7 + 3)/5','Pa')})"
        seen=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt);seen.append(data);response=deepcopy(self.responses['dbc_recover']);response['call_id']=data['call']['call_id'];response['stage']=data['call']['stage']
            if len(seen)==1:response['artifact']={'kind':'dbc','format':'constructor','content':content}
            else:
                base=data['contract_repair_base'];self.assertEqual(data['output_template']['artifact']['format'],'constructor_edits')
                response['artifact']={'kind':'dbc','format':'constructor_edits','base_material_id':base['material_id'],'base_sha256':base['sha256'],
                    'edits':[{'operation':'replace','path':['quantities','bad','expression'],'value':'raw/2'}]}
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),self.root/'calls',client=client)
        response,checks,_=runner.recover(self.requests['dbc_recover'])
        self.assertEqual(len(seen),2);self.assertNotEqual(checks['semantic_validation']['status'],'violated')
        original=parse(content,'dbc');patched=parse(response['artifact']['content'],'dbc')
        self.assertEqual(patched['quantities']['keep'],original['quantities']['keep']);self.assertEqual(patched['quantities']['bad']['expression'],'raw/2')
        self.assertTrue((runner.output/(seen[-1]['call']['call_id']+'.constructor-normalization.json')).is_file())

    def test_rejected_field_edits_keep_the_complete_exact_base_for_bounded_format_repair(self):
        request,messages,template=self.repair();seen=[];initial_base=json.loads(messages[-1]['content'])['contract_repair_base']
        def client(prompt,**kwargs):
            data=json.loads(prompt);seen.append(data);base=data['contract_repair_base']
            self.assertEqual(base['material_id'],initial_base['material_id']);self.assertEqual(base['sha256'],initial_base['sha256'])
            self.assertTrue(any(c['role']=='candidate' and c['text'].startswith('DeviceContract(') for c in data['context']))
            response=deepcopy(template);response['call_id']=data['call']['call_id'];response['artifact'].update(base_material_id=base['material_id'],base_sha256=base['sha256'])
            if len(seen)==1:
                response['artifact']['edits']=[{'operation':'replace','path':['unresolved'],'value':['remaining gap']},
                    {'operation':'replace','path':['unresolved',0],'value':'overlap'}]
            else:
                diagnostic=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
                self.assertIn('rejected_contract_edits',diagnostic);self.assertEqual(diagnostic['retained_base']['sha256'],base['sha256'])
            return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),self.root/'field-retry',client=client)
        response,checks,_=runner.recover(request)
        self.assertEqual(len(seen),2);self.assertEqual(checks['semantic_validation']['status'],'unknown')
        self.assertEqual(parse(response['artifact']['content'],'dbc')['unresolved'],('original unresolved facts retained','new explicit gap'))

    def test_malformed_repair_envelope_also_retains_the_existing_exact_contract(self):
        request,messages,template=self.repair();seen=[];initial=json.loads(messages[-1]['content'])['contract_repair_base']
        def client(prompt,**kwargs):
            data=json.loads(prompt);seen.append(data)
            if len(seen)==1:return '{malformed output'
            self.assertEqual(data['contract_repair_base']['sha256'],initial['sha256'])
            diagnostic=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
            self.assertEqual(diagnostic['rejected_response_text'],'{malformed output')
            response=deepcopy(template);response['call_id']=data['call']['call_id'];return json.dumps(response)
        runner=CallRunner(Evidence(self.visible,self.registry),self.root/'opaque-retry',client=client)
        response,checks,_=runner.recover(request)
        self.assertEqual(len(seen),2);self.assertEqual(checks['semantic_validation']['status'],'unknown')
        self.assertEqual(parse(response['artifact']['content'],'dbc')['unresolved'],('original unresolved facts retained','new explicit gap'))
