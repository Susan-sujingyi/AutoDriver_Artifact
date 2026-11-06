"""Synthetic offline checks for production context scopes and explicit unknown bindings."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchmark.common.api import ROOT
from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.calls import CallRunner
from method.autodriver.implementation.system.tools import ToolExecutor
from method.autodriver.implementation.system.protocol.assembler import assemble,digest,ContextLimitError,ProtocolError
from method.autodriver.implementation.system.contract.model import parse,identity
from method.autodriver.implementation.system.contract.binding import check_binding
import method.autodriver.implementation.system.calls as calls_module


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.visible,self.registry,self.requests,self.responses=make_fixture(Path(self.temp.name))
        document=next(e for e in self.registry['materials'] if e['id']=='document')
        original=copy.deepcopy(document);original.update(id='document.original',path='input/original-document.txt')
        (self.visible/original['path']).write_bytes((self.visible/document['path']).read_bytes())
        self.registry['materials'].append(original)
        document.update(kind='document_extract',parents=['document.original'])
        self.seen=[]

    def cap(self,stage='dbc_recover'):
        request=copy.deepcopy(self.requests[stage]);_,audit=assemble(self.visible,self.registry,request)
        request['max_context_bytes']=audit['utf8_bytes']+16384
        return request

    def replace(self,identifier,text,path=None):
        entry=next(e for e in self.registry['materials'] if e['id']==identifier)
        if path is not None:entry['path']=path
        output=self.visible/entry['path'];output.parent.mkdir(parents=True,exist_ok=True);output.write_text(text)
        entry['sha256']=digest(text.encode())

    def huge_document(self):
        text='[Original PDF page 1]\n'+'SYNTHETIC filler only\n'*10000
        text+='\nSYNTHETIC_NEEDLE complete paragraph.\nSYNTHETIC footnote applies to that paragraph.\n\n'
        text+='SYNTHETIC filler only\n'*10000
        self.replace('document',text);return text

    def candidate(self,payload,stage='dbc_recover'):
        value=copy.deepcopy(self.responses[stage]);value.update(call_id=payload['call']['call_id'],stage=payload['call']['stage'])
        return value

    def runner(self,client,variant='full'):
        return CallRunner(Evidence(self.visible,self.registry),self.visible/'output/requests',client=client,variant=variant)

    def test_index_then_complete_passage_without_raising_cap_or_losing_original(self):
        request=self.cap();original=self.huge_document()
        def client(prompt,**kwargs):
            payload=json.loads(prompt);self.seen.append(payload)
            value=self.candidate(payload)
            if len(self.seen)==1:
                index=next(c for c in payload['context'] if c['kind']=='document_index')
                self.assertFalse(index['evidence_eligible'])
                value.update(outcome='needs_evidence',artifact=None,evidence_links=[],context_requests=[{
                    'scope':'device_documents','query':'SYNTHETIC_NEEDLE','question':'retrieve the complete synthetic paragraph and its footnote','reason':'index is not evidence'}])
            else:
                passages=[c for c in payload['context'] if c['material_id']=='document' and c['evidence_eligible']]
                self.assertTrue(any('SYNTHETIC_NEEDLE' in c['text'] and 'footnote' in c['text'] for c in passages))
            return json.dumps(value)
        runner=self.runner(client);_,_,history=runner.recover(request)
        self.assertEqual(len(history),2);self.assertEqual(len(self.seen),2)
        self.assertEqual(runner.last_request['max_context_bytes'],request['max_context_bytes'])
        for path in (self.visible/'output/requests').glob('*.audit.json'):
            self.assertLessEqual(json.loads(path.read_text())['utf8_bytes'],request['max_context_bytes'])
        self.assertEqual(runner.evidence.read('document').decode(),original)
        self.assertTrue(all(any(c['material_id']=='source' for c in p['context']) for p in self.seen))

    def test_index_cannot_be_cited_as_a_fact(self):
        request=self.cap();self.huge_document()
        def client(prompt,**kwargs):
            payload=json.loads(prompt);self.seen.append(payload);value=self.candidate(payload)
            index=next(c for c in payload['context'] if c['kind']=='document_index')
            value['evidence_links']=[{'claim':'synthetic invalid assertion','basis':'not original evidence','citations':[index['citation_id']]}]
            return json.dumps(value)
        with self.assertRaises(ProtocolError):self.runner(client).call(request)
        self.assertEqual(len(self.seen),1)

    def test_index_drives_complete_page_and_distant_footnote_recovery(self):
        request=self.cap()
        original='[Original PDF page 1]\n'+'SYNTHETIC irrelevant content\n'*18000
        page='[Original PDF page 2]\nSYNTHETIC table header\n'+''.join('row '+str(i)+'\n' for i in range(40))
        page+='SYNTHETIC footnote at the end of the entire table\n'
        original+=page+'[Original PDF page 3]\nSYNTHETIC another page\n'
        self.replace('document',original)
        def client(prompt,**kwargs):
            payload=json.loads(prompt);self.seen.append(payload);value=self.candidate(payload)
            if len(self.seen)==1:
                index=json.loads(next(c['text'] for c in payload['context'] if c['kind']=='document_index'))
                record=next(c for c in index['complete_materials'] if c['material_id']=='document')
                selected=next(p for p in record['pages'] if p['page']==2)
                arguments={'scope':'device_documents','material_id':'document',
                    'start_line':selected['start_line'],'end_line':selected['end_line']}
                value.update(outcome='needs_evidence',artifact=None,evidence_links=[],context_requests=[{
                    'scope':'internal_tools','query':json.dumps({'tool':'read_evidence_range','arguments':arguments}),
                    'question':'Read the complete synthetic table and footnote together','reason':'Index is not evidence'}])
            else:
                excerpts=[c for c in payload['context'] if c['material_id']=='document' and c['evidence_eligible']]
                self.assertEqual(len(excerpts),1);self.assertEqual(excerpts[0]['text'],page)
            return json.dumps(value)
        runner=self.runner(client);_,_,history=runner.recover(request)
        self.assertEqual(len(history),2);self.assertEqual(runner.evidence.read('document').decode(),original)
        self.assertEqual(runner.last_request['max_context_bytes'],request['max_context_bytes'])
        audit=json.loads((runner.output/(history[0]+'.retrieval.json')).read_text())
        self.assertEqual(audit['tools'][0]['tool'],'read_evidence_range')
        self.assertEqual(audit['tools'][0]['status'],'returned')

    def test_range_reader_refuses_scope_contract_and_coordinate_escape(self):
        executor=ToolExecutor(Evidence(self.visible,self.registry),self.requests['fp_build'])
        self.assertEqual(executor.specs()['read_evidence_range']['properties']['scope']['enum'],['source_framework','target_framework'])
        base={'scope':'target_framework','material_id':'target_framework','start_line':1,'end_line':2}
        self.assertEqual(len(executor.execute('read_evidence_range',base)['excerpts']),1)
        rejected=[dict(base,scope='device_documents',material_id='document'),
            dict(base,material_id='source_framework'),dict(base,material_id='../../private'),
            dict(base,material_id='target_fp'),dict(base,start_line=True),
            dict(base,start_line=0),dict(base,start_line=2,end_line=1),dict(base,end_line=6001)]
        for arguments in rejected:
            with self.subTest(arguments=arguments),self.assertRaises(ValueError):
                executor.execute('read_evidence_range',arguments)
        # Per-call enum customization must not mutate the shared schemas.
        device=ToolExecutor(executor.evidence,self.requests['dbc_recover'])
        self.assertIn('device_documents',device.specs()['read_evidence_range']['properties']['scope']['enum'])

    def test_protected_retrieved_evidence_is_not_replaced_by_an_index(self):
        request=self.cap();self.huge_document();runner=self.runner(lambda *a,**k:self.fail('client must not be invoked'))
        with self.assertRaises(ContextLimitError):runner.call(request,protected_materials={'document'})
        self.assertEqual(runner.calls,[])

    def test_raw_device_source_is_not_clipped_to_force_a_fit(self):
        request=self.cap();self.replace('source','/* SYNTHETIC raw source */\n'*20000)
        runner=self.runner(lambda *a,**k:self.fail('client must not be invoked'))
        with self.assertRaises(ContextLimitError):runner.call(request)
        self.assertEqual(runner.calls,[])

    def test_framework_index_preserves_role_and_fetches_a_complete_declaration(self):
        request=self.cap('fp_build')
        self.replace('target_framework','int synthetic_submit(void) {return 0;}\n/*\n'+'SYNTHETIC unused comment\n'*20000+'*/\n','framework/target_api.c')
        def client(prompt,**kwargs):
            payload=json.loads(prompt);self.seen.append(payload);value=self.candidate(payload,'fp_build')
            if len(self.seen)==1:
                index=next(c for c in payload['context'] if c['kind']=='framework_index')
                self.assertFalse(index['evidence_eligible'])
                value.update(outcome='needs_evidence',artifact=None,evidence_links=[],context_requests=[{
                    'scope':'target_framework','query':'synthetic_submit','question':'read the complete declaration','reason':'index is not a framework guarantee'}])
            else:self.assertTrue(any('synthetic_submit' in c['text'] for c in payload['context'] if c['kind']=='framework_source'))
            return json.dumps(value)
        _,_,history=self.runner(client).recover(request)
        self.assertEqual(len(history),2)

    def test_without_source_lifting_never_introduces_structured_source_fp(self):
        request=self.cap();request['selections']=[s for s in request['selections'] if s['role']!='source_fp'];self.huge_document()
        def client(prompt,**kwargs):
            payload=json.loads(prompt);self.seen.append(payload)
            self.assertNotIn('source_fp',[c['role'] for c in payload['context']])
            self.assertFalse(any(c['kind'] in {'fp_shared','fp_task_local'} for c in payload['context']))
            return json.dumps(self.candidate(payload))
        self.runner(client,'without_source_framework_lifting').recover(request)
        self.assertEqual(len(self.seen),1)

    def test_source_analysis_scope_preserves_every_gap_and_raw_source(self):
        request=self.cap();graph={'functions':{'f':{'name':'acquire','material_id':'source','start_line':1,'end_line':2}},
            'calls':[],'gaps':[{'reason':'synthetic unresolved alias'}],'configuration':{},'dependencies':[{'from':'f','to':'f'}]*20000}
        text=json.dumps({'program_dependencies':graph,'gaps':[{'reason':'synthetic syntax gap'}]})
        output=self.visible/'analysis/source.json';output.parent.mkdir();output.write_text(text)
        self.registry['materials'].append({'id':'analysis','path':'analysis/source.json','kind':'source_analysis','sha256':digest(text.encode()),'approved':True,'parents':['source'],'os_role':'source'})
        request['selections'].append({'role':'source_facts','material_id':'analysis'})
        def client(prompt,**kwargs):
            payload=json.loads(prompt);self.seen.append(payload);index=next(c for c in payload['context'] if c['kind']=='source_index')
            self.assertFalse(index['evidence_eligible']);view=json.loads(index['text'])
            self.assertEqual(view['complete_materials'][0]['index']['gaps'],graph['gaps'])
            self.assertTrue(any(c['material_id']=='source' for c in payload['context']))
            return json.dumps(self.candidate(payload))
        self.runner(client).recover(request);self.assertEqual(len(self.seen),1)


class UnknownBindingTests(unittest.TestCase):
    def report(self,mechanism,buffer='None'):
        dbc=parse("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),WaitAfterCompletion('send','1 ms')))",'dbc')
        fp=parse("FrameworkProfile('synthetic','frozen')",'fp');support={}
        bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',primitive_bindings={'WaitAfterCompletion':"+mechanism+"},io_buffer="+buffer+")",'bp')
        return check_binding(dbc,fp,bp,support,parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity(support)})

    def test_explicit_unknown_binding_is_a_finding(self):
        report=self.report("Unresolved('synthetic missing provider')")
        self.assertEqual(report['status'],'unknown');self.assertTrue(any(f['check']=='binding_recovered' for f in report['findings']))

    def test_unknown_buffer_is_a_finding(self):
        report=self.report("Unresolved('synthetic')","Unresolved('synthetic buffer')")
        self.assertTrue(any(f['check']=='buffer_recovered' and f['status']=='unknown' for f in report['findings']))

    def test_invalid_mechanism_remains_a_violation(self):
        self.assertEqual(self.report("'not a mechanism'")['status'],'violated')

    def test_unknown_entire_mapping_does_not_become_false_missing_coverage(self):
        dbc=parse("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),WaitAfterCompletion('send','1 ms')))",'dbc')
        fp=parse("FrameworkProfile('synthetic','frozen')",'fp');support={}
        bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',primitive_bindings=Unresolved('synthetic map'))",'bp')
        report=check_binding(dbc,fp,bp,support,parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity(support)})
        self.assertEqual(report['status'],'unknown')
        self.assertFalse(any(f['check']=='action_coverage' and f['status']=='violated' for f in report['findings']))

    def test_malformed_entire_mapping_is_a_violation(self):
        dbc=parse("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),WaitAfterCompletion('send','1 ms')))",'dbc')
        fp=parse("FrameworkProfile('synthetic','frozen')",'fp');support={}
        for value in ("'not a map'",'None'):
            with self.subTest(value=value):
                bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',primitive_bindings="+value+")",'bp')
                report=check_binding(dbc,fp,bp,support,parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity(support)})
                self.assertEqual(report['status'],'violated')
