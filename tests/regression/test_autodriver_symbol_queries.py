"""Exact multi-root recovery, missing roots and non-factual query indices."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.tools import ToolExecutor
from method.autodriver.implementation.system.calls import CallRunner
from method.autodriver.implementation.system.protocol.assembler import digest,assemble,check_response,ProtocolError
from method.autodriver.implementation.system.variants import admit_call


class SymbolQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root,self.registry,self.requests,self.responses=make_fixture(self.temp.name)
        entry=next(e for e in self.registry['materials'] if e['id']=='target_framework')
        entry['path']='framework/target_api.c'
        text='int first_native(void) { return 1; }\nint second_native(void) { return 2; }\n'
        (self.root/entry['path']).write_text(text);entry['sha256']=digest(text.encode())

    def test_array_and_whitespace_selectors_fetch_complete_known_declarations(self):
        executor=ToolExecutor(Evidence(self.root,self.registry),self.requests['dbc_recover'])
        for selectors in (['first_native','second_native'],'first_native second_native'):
            with self.subTest(selectors=selectors):
                result=executor.execute('inspect_symbol',{'scope':'target_framework','symbol':selectors})
                text=''.join(row['text'] for row in result['excerpts'])
                self.assertIn('int first_native(void) { return 1; }',text)
                self.assertIn('int second_native(void) { return 2; }',text)
                self.assertEqual(result['roots'],['first_native','second_native'])

    def test_unknown_spelling_and_namespaces_stay_missing_in_the_audit(self):
        executor=ToolExecutor(Evidence(self.root,self.registry),self.requests['dbc_recover'])
        roots=['first_native','UnknownType','WrongNamespace::second_native']
        result=executor.execute('inspect_symbol',{'scope':'target_framework','symbol':roots})
        self.assertEqual({row['symbol'] for row in result['gaps']},{'UnknownType','WrongNamespace::second_native'})
        self.assertEqual(executor.events[-1]['index_result']['gaps'],result['gaps'])
        self.assertFalse(result['complete']);self.assertTrue(result['dependencies'])
        self.assertIn('second_native',result['exact_indexed_names'])

    def test_root_bounds_and_forbidden_scopes_fail_before_source_access(self):
        executor=ToolExecutor(Evidence(self.root,self.registry),self.requests['fp_build'])
        for arguments in ({'scope':'source','symbol':'first_native'},
                {'scope':'target_framework','symbol':' '.join(['first_native']*17)},
                {'scope':'target_framework','symbol':['first_native']*17},
                {'scope':'target_framework','symbol':'   '}):
            with self.subTest(arguments=arguments),self.assertRaises(ValueError):
                executor.execute('inspect_symbol',arguments)

    def test_recovery_returns_missing_root_index_without_allowing_it_as_evidence(self):
        seen=[];request=copy.deepcopy(self.requests['code_generate'])
        def client(prompt,**kwargs):
            data=json.loads(prompt);seen.append(data)
            result=copy.deepcopy(self.responses['code_generate'])
            result.update(call_id=data['call']['call_id'],stage=data['call']['stage'])
            if len(seen)==1:
                result.update(outcome='needs_evidence',artifact=None,evidence_links=[],context_requests=[{
                    'scope':'internal_tools','query':json.dumps({'tool':'inspect_symbol','arguments':{
                        'scope':'source','symbol':'UnknownSourceType'}}),
                    'question':'Find the exact source declaration','reason':'Do not infer a missing type from another OS'}])
            else:
                index=next(c for c in data['context'] if c['kind']=='source_index')
                metadata=json.loads(index['text'])
                self.assertFalse(index['evidence_eligible']);self.assertFalse(metadata['facts_available'])
                self.assertEqual(metadata['gaps'][0]['symbol'],'UnknownSourceType')
                self.assertTrue(metadata['exact_indexed_names'])
            return json.dumps(result)
        runner=CallRunner(Evidence(self.root,self.registry),self.root/'output/requests',client=client)
        result,_,history=runner.recover(request)
        self.assertEqual(len(history),2);self.assertEqual(len(seen),2)
        index=next(c for c in seen[-1]['context'] if c['kind']=='source_index')
        invalid=copy.deepcopy(result);invalid['evidence_links']=[{'claim':'missing source type exists','basis':'index',
            'citations':[index['citation_id']]}]
        with self.assertRaises(ProtocolError):check_response(invalid,runner.last_request,runner.last_messages)
        # A source lookup index is available at initial generation too; it
        # cannot carry candidate-check feedback into compile-only admission.
        request['selections'].append({'role':'source_facts','material_id':index['material_id']})
        registry=dict(self.registry,materials=list(runner.evidence.registry.values()))
        admit_call('compile_only_repair',request,registry)
        messages,_=assemble(self.root,registry,request)
        self.assertFalse(next(c for c in json.loads(messages[1]['content'])['context'] if c['kind']=='source_index')['evidence_eligible'])
