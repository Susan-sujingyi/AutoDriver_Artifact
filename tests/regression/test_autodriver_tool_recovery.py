"""Actual bounded analysis retrieval, with fake model envelopes only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.calls import CallRunner
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.context_tables import expand
from method.autodriver.implementation.system.protocol.assembler import digest


class ToolRecoveryTests(unittest.TestCase):
    def test_repeated_existing_excerpt_gets_bounded_stall_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            visible,registry,requests,responses=make_fixture(temp);seen=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data)
                response=deepcopy(responses['dbc_recover']);response['call_id']=data['call']['call_id']
                if len(seen)<=2:
                    response.update(outcome='needs_evidence',artifact=None,context_requests=[{
                        'scope':'source','query':'example_acquire','question':'Same original source','reason':'Offline repeated existing excerpt'}])
                else:
                    diagnostics=[json.loads(c['text']) for c in data['context'] if c['role']=='internal_diagnostic']
                    stalled=next(d for d in diagnostics if d.get('new_evidence') is False)
                    self.assertEqual(stalled['status'],'unknown');self.assertIn('partial candidate',stalled['instruction'])
                return json.dumps(response)
            runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client)
            self.assertTrue(runner.recover(requests['dbc_recover'])[1]['transport_valid'])
            self.assertEqual(len(seen),3)

    def test_long_json_in_literal_scope_is_unknown_feedback_not_a_command_or_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            visible,registry,requests,responses=make_fixture(temp);seen=[]
            query=json.dumps({'tool':'resolve_configuration','arguments':{'roots':['synthetic_symbol_'+str(i) for i in range(20)]}},separators=(',',':'))
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data)
                response=deepcopy(responses['dbc_recover']);response['call_id']=data['call']['call_id']
                if len(seen)==1:
                    response.update(outcome='needs_evidence',artifact=None,context_requests=[{
                        'scope':'source_framework','query':query,'question':'Resolve synthetic symbols','reason':'Offline malformed literal request'}])
                else:
                    diagnostics=[json.loads(c['text']) for c in data['context'] if c['role']=='internal_diagnostic']
                    refusal=next(d for d in diagnostics if d.get('query',{}).get('query')==query)
                    self.assertEqual(refusal['status'],'unknown');self.assertIn('bounded literal',refusal['reason'])
                    self.assertFalse(any(c['role']=='source_facts' for c in data['context']))
                return json.dumps(response)
            runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client)
            self.assertTrue(runner.recover(requests['dbc_recover'])[1]['transport_valid'])
            self.assertEqual(len(seen),2)

    def test_empty_lookup_reports_approved_inventory_and_code_continuations_use_repair_cap(self):
        from method.autodriver.implementation.system.protocol.assembler import MethodLimitError
        for exhausted in (False,True):
            with self.subTest(exhausted=exhausted),tempfile.TemporaryDirectory() as temp:
                visible,registry,requests,responses=make_fixture(temp);seen=[]
                def client(prompt,**kwargs):
                    data=json.loads(prompt);seen.append(data);call=data['call']
                    response=deepcopy(responses['code_generate']);response.update(call_id=call['call_id'],stage=call['stage'])
                    if len(seen)>1:
                        self.assertEqual(call['stage'],'repair');self.assertEqual(call['repair_target'],'code')
                        diagnostics=[json.loads(c['text']) for c in data['context'] if c['role']=='internal_diagnostic']
                        misses=[d for d in diagnostics if 'approved_materials' in d];self.assertTrue(misses)
                        self.assertEqual(misses[-1]['matches'],0)
                        self.assertTrue(any(m['material_id']=='source' for m in misses[-1]['approved_materials']))
                    if exhausted or len(seen)==1:
                        response.update(outcome='needs_evidence',artifact=None,context_requests=[{
                            'scope':'source','query':'unprovided_symbol_xyz','question':'Locate requested symbol',
                            'reason':'Synthetic missing lookup; no private sources'}])
                    return json.dumps(response)
                runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client,maximum_refinements=8)
                if exhausted:
                    with self.assertRaises(MethodLimitError):runner.recover(requests['code_generate'])
                    self.assertEqual(len(seen),5)
                else:
                    response,checks,_=runner.recover(requests['code_generate'])
                    self.assertEqual(response['outcome'],'candidate');self.assertTrue(checks['transport_valid'])
                    self.assertEqual(len(seen),2)

    def test_unprovided_citation_is_repaired_using_current_local_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            visible,registry,requests,responses=make_fixture(temp);seen=[]
            requests['bp_build']['selections'].insert(0,{'role':'existing_bp','material_id':'bp'})
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data);response=deepcopy(responses['bp_build']);response.update(call_id=data['call']['call_id'],stage=data['call']['stage'])
                if len(seen)==1:handle='c-not-provided'
                else:
                    diagnostic=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
                    inventory=diagnostic['source_citation_inventory'];self.assertTrue(inventory)
                    actual={c['citation_id']:c for c in data['context']}
                    self.assertTrue(all(row['citation_id'] in actual for row in inventory))
                    for row in inventory:
                        for key in ('material_id','kind','start_line','end_line'):self.assertEqual(row[key],actual[row['citation_id']][key])
                    handle=next(c['citation_id'] for c in data['context'] if c['role']=='dbc')
                    self.assertIn('untrusted',diagnostic['citation_instruction'])
                response['evidence_links']=[{'claim':'synthetic binding input','basis':'offline protocol fixture','citations':[handle]}]
                return json.dumps(response)
            runner=CallRunner(Evidence(visible,registry),Path(temp)/'citation-repair',client=client)
            _,checks,_=runner.recover(requests['bp_build'])
            self.assertEqual(len(seen),2);self.assertTrue(checks['transport_valid'])

    def test_overlapping_questions_receive_one_complete_union_graph(self):
        from method.autodriver.implementation.system.tools import ToolExecutor
        with tempfile.TemporaryDirectory() as temp:
            visible,registry,requests,responses=make_fixture(temp)
            entry=next(e for e in registry['materials'] if e['id']=='source');path=visible/entry['path']
            path.write_text('int helper(int n){if(n<0)return -1;return n*2;}\nint acquire(void){return helper(example_acquire());}\nint initialize(void){return helper(3);}\nint unrelated(void){return 9;}\n')
            entry['sha256']=digest(path.read_bytes());seen=[]
            queries=[{'scope':'internal_tools','question':'Complete '+root,'reason':'Whole source effect closure',
                'query':json.dumps({'tool':'slice_device_effects','arguments':{'roots':[root]}})} for root in ('acquire','initialize')]
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data);response=deepcopy(responses['dbc_recover']);response['call_id']=data['call']['call_id']
                if len(seen)==1:response.update(outcome='needs_evidence',artifact=None,context_requests=queries)
                else:
                    graphs=[expand(yaml.safe_load(c['text'])) for c in data['context'] if c['role']=='source_facts']
                    self.assertEqual(len(graphs),1);graph=graphs[0]
                    self.assertEqual(set(graph['roots']),{'acquire','initialize'})
                    self.assertEqual({f['name'] for f in graph['functions'].values()},{'acquire','initialize','helper'})
                    executor=ToolExecutor(Evidence(visible,registry),requests['dbc_recover'])
                    for root in ('acquire','initialize'):
                        original=executor.execute('slice_device_effects',{'roots':[root]})
                        for key in ('functions','facts'):
                            self.assertTrue(set(original[key])<=set(graph[key]))
                            for identifier,value in original[key].items():self.assertEqual(value,graph[key][identifier])
                        for edge in original['dependencies']:self.assertIn(edge,graph['dependencies'])
                return json.dumps(response)
            runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client)
            self.assertTrue(runner.recover(requests['dbc_recover'])[1]['transport_valid'])
            audit=json.loads((runner.output/(requests['dbc_recover']['call_id']+'.retrieval.json')).read_text())
            self.assertEqual(len(audit['tools']),1);self.assertEqual(audit['source_query_batches'][0]['member_indices'],[0,1])

    def test_union_failure_preserves_the_other_independent_answer(self):
        with tempfile.TemporaryDirectory() as temp:
            visible,registry,requests,responses=make_fixture(temp);seen=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data);response=deepcopy(responses['dbc_recover']);response['call_id']=data['call']['call_id']
                if len(seen)==1:
                    response.update(outcome='needs_evidence',artifact=None,context_requests=[{'scope':'internal_tools','question':'Whole source closure',
                        'reason':'Independent query','query':json.dumps({'tool':'slice_device_effects','arguments':{'roots':[root]}})} for root in ('acquire','missing')])
                else:
                    graph=expand(yaml.safe_load(next(c['text'] for c in data['context'] if c['role']=='source_facts')))
                    self.assertIn('acquire',{f['name'] for f in graph['functions'].values()})
                    refusal=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
                    self.assertEqual(refusal['status'],'unknown');self.assertIn('missing',refusal['reason'])
                return json.dumps(response)
            runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client)
            self.assertTrue(runner.recover(requests['dbc_recover'])[1]['transport_valid'])
            audit=json.loads((runner.output/(requests['dbc_recover']['call_id']+'.retrieval.json')).read_text())
            self.assertEqual(audit['source_query_batches'][0]['status'],'fallback_to_individual_queries')

    def test_framework_projection_covers_external_calls_outside_the_requested_slice(self):
        with tempfile.TemporaryDirectory() as temp:
            visible,registry,requests,responses=make_fixture(temp)
            source=next(e for e in registry['materials'] if e['id']=='source')
            (visible/source['path']).write_text('int acquire(void) {return example_acquire();}\nint other(void) {return other_api();}\n')
            source['sha256']=digest((visible/source['path']).read_bytes())
            framework=next(e for e in registry['materials'] if e['id']=='source_framework')
            framework['path']='framework/source_api.h'
            (visible/framework['path']).write_text('int example_acquire(void);\nint other_api(void);\nint unrelated_api(void);\n')
            framework['sha256']=digest((visible/framework['path']).read_bytes())
            requests['dbc_recover']['selections'].append({'role':'framework_evidence','material_id':'source_framework'})
            seen=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data);response=deepcopy(responses['dbc_recover']);response['call_id']=data['call']['call_id']
                if len(seen)==1:
                    response.update(outcome='needs_evidence',artifact=None,context_requests=[{'scope':'internal_tools','question':'Inspect acquire',
                        'query':json.dumps({'tool':'slice_device_effects','arguments':{'roots':['acquire']}}),'reason':'Need its whole source effect graph'}])
                else:
                    graph=expand(yaml.safe_load(next(c['text'] for c in data['context'] if c['role']=='source_facts')))
                    self.assertEqual({f['name'] for f in graph['functions'].values()},{'acquire'})
                    self.assertEqual(set(graph['source_api_retrieval']['roots']),{'example_acquire','other_api'})
                    excerpts=[c for c in data['context'] if c['material_id']=='source_framework']
                    self.assertTrue(excerpts);text='\n'.join(c['text'] for c in excerpts)
                    self.assertIn('other_api',text);self.assertNotIn('unrelated_api',text)
                    self.assertEqual(next(c['text'] for c in data['context'] if c['material_id']=='source'),(visible/source['path']).read_text())
                return json.dumps(response)
            runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client)
            self.assertTrue(runner.recover(requests['dbc_recover'])[1]['transport_valid']);self.assertEqual(len(seen),2)
            record=json.loads((runner.output/(requests['dbc_recover']['call_id']+'.retrieval.json')).read_text())
            self.assertEqual(set(record['source_framework_projection']['roots']),{'example_acquire','other_api'})

    def test_model_question_executes_complete_control_data_callee_closure(self):
        with tempfile.TemporaryDirectory() as temp:
            visible,registry,requests,responses=make_fixture(temp)
            entry=next(e for e in registry['materials'] if e['id']=='source');path=visible/entry['path']
            path.write_text('int helper(int v) {return v*3;}\nint acquire(void) {int r=example_acquire(); if(r<0) return r; return helper(r);}\n')
            entry['sha256']=digest(path.read_bytes());seen=[]
            index=visible/'analysis/review-index.json';index.parent.mkdir();index.write_text('{"scope":"synthetic review index"}\n')
            registry['materials'].append({'id':'source.review-index','path':'analysis/review-index.json','kind':'source_analysis','approved':True,
                'parents':['source'],'os_role':'source','sha256':digest(index.read_bytes()),'origin_locator':'synthetic index fixture'})
            requests['dbc_recover']['selections'].append({'role':'source_facts','material_id':'source.review-index'})
            def client(prompt,**kwargs):
                data=json.loads(prompt);seen.append(data);response=deepcopy(responses['dbc_recover']);response['call_id']=data['call']['call_id']
                if len(seen)==1:
                    response.update(outcome='needs_evidence',artifact=None,context_requests=[{'scope':'internal_tools','question':'Recover source return/failure dependencies',
                        'query':json.dumps({'tool':'slice_device_effects','arguments':{'roots':['acquire']}}),'reason':'Need complete function and callee closure'}])
                else:
                    self.assertNotIn('source.review-index',[c['material_id'] for c in data['context']])
                    result=expand(yaml.safe_load(next(c['text'] for c in data['context'] if c['role']=='source_facts')))
                    self.assertEqual({f['name'] for f in result['functions'].values()},{'acquire','helper'})
                    self.assertTrue(any(e['kind']=='return_dependency' for e in result['dependencies']))
                    self.assertTrue(any(f['kind']=='guard' for f in result['facts'].values()))
                    self.assertTrue(any(f['kind']=='return' and f['control'] for f in result['facts'].values()))
                    self.assertIn('unknown',result['semantic_effects'])
                return json.dumps(response)
            runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client)
            response,checks,history=runner.recover(requests['dbc_recover'])
            self.assertEqual(len(seen),2);self.assertEqual(response['outcome'],'candidate');self.assertEqual(checks['semantic_validation']['status'],'unknown')
            retrieval=json.loads((runner.output/(requests['dbc_recover']['call_id']+'.retrieval.json')).read_text())
            self.assertEqual(retrieval['tools'][0]['tool'],'slice_device_effects')
            self.assertTrue(retrieval['added_selections'])

    def test_unknown_tool_or_root_is_explicitly_refused_without_shell_fallback(self):
        for command in ({'tool':'exec','arguments':{'cmd':'forbidden'}},{'tool':'slice_device_effects','arguments':{'roots':['missing']}}):
            with self.subTest(command=command),tempfile.TemporaryDirectory() as temp:
                visible,registry,requests,responses=make_fixture(temp);seen=[]
                def client(prompt,**kwargs):
                    data=json.loads(prompt);seen.append(data);response=deepcopy(responses['dbc_recover']);response['call_id']=data['call']['call_id']
                    if len(seen)==1:response.update(outcome='needs_evidence',artifact=None,context_requests=[{'scope':'internal_tools','question':'synthetic failed query',
                        'query':json.dumps(command),'reason':'synthetic negative test'}])
                    else:
                        report=json.loads(next(c['text'] for c in data['context'] if c['material_id'].endswith('.tool-result.0')))
                        self.assertEqual(report['status'],'unknown');self.assertIn('refused',report['scope'])
                    return json.dumps(response)
                runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client)
                self.assertTrue(runner.recover(requests['dbc_recover'])[1]['transport_valid']);self.assertEqual(len(seen),2)
