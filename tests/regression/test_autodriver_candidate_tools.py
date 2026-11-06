"""Finite host tool request boundary using fake compilers and native output."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from benchmark.common.api import ROOT,save,digest
from method.autodriver.implementation.system.protocol.assembler import canonical,digest as sha
from tests.common.autodriver_tools import CandidateTools


class CandidateToolsTests(unittest.TestCase):
    def test_final_freeze_binds_last_actual_bytes_and_refuses_drift_or_new_checks(self):
        for drift in (False,True):
            with self.subTest(drift=drift),tempfile.TemporaryDirectory(dir=ROOT/'run') as temp:
                root=Path(temp);service,request=self.fixture(root)
                def prepare(agent,baseline,candidate,proposal):
                    (candidate/'target').mkdir(parents=True);(candidate/'target/driver.c').write_text('synthetic candidate\n')
                    (candidate/'output/submission').mkdir(parents=True);(candidate/'output/submission/driver.patch').write_text('synthetic patch\n')
                    save(candidate/'output/submission/manifest.json',{'scope':'offline fixture only'})
                    record={'revision':proposal['revision'],'changes':{'target/driver.c':digest(candidate/'target/driver.c')}}
                    save(candidate/'candidate.json',record);return record
                with patch('tests.common.autodriver_tools.prepare_candidate',side_effect=prepare),\
                     patch('tests.common.autodriver_tools.public_compile',return_value={'status':'pass','commands':[],'execution':{'cleanup':{'status':'pass'}}}),\
                     patch('tests.common.autodriver_tools.native_nuttx',return_value={'status':'unknown','native_candidate_executed':False}):
                    service.check(request)
                if drift:
                    (service.last_candidate/'target/driver.c').write_text('changed after observation')
                    with self.assertRaisesRegex(ValueError,'bytes changed'):service.freeze_final(root/'freeze.json')
                else:
                    with self.assertRaisesRegex(ValueError,'older candidate'):service.freeze_final(root/'wrong-freeze.json',expected_revision='0'*64)
                    frozen=service.freeze_final(root/'freeze.json')
                    self.assertEqual(frozen['files']['target/driver.c'],digest(service.last_candidate/'target/driver.c'))
                    with self.assertRaisesRegex(ValueError,'frozen'):service.check(request)
                    service.archive(root/'archive')
                    self.assertEqual((root/'archive/candidate-1/source/target/driver.c').read_text(),'synthetic candidate\n')

    def fixture(self,root,variant='full'):
        agent=root/'agent';directory=agent/'output/autodriver';directory.mkdir(parents=True);baseline=root/'host/baseline';baseline.mkdir(parents=True)
        save(agent/'task.yaml',{'task_id':'synthetic','target':{'os':'nuttx'}});save(agent/'materials.json',{'identity':'frozen','files':{}})
        artifact={'kind':'code','format':'file_edits','edits':[]}
        proposal={'revision':'1'*64,'applied':False,'full_scope_gate':'open','artifact':artifact}
        state={'schema':'autodriver.contract-state/v1','task_id':'synthetic','input_identity':'frozen','variant':variant,
            'materials':[{'id':'code','text':canonical(artifact),'sha256':sha(canonical(artifact).encode())}],
            'objects':{'code':{'material_id':'code'}}}
        save(directory/'code-proposal.json',proposal);save(directory/'contract-state.json',state)
        request={'schema':'autodriver.candidate-check-request/v1','revision':'1'*64,
            'proposal_sha256':digest(directory/'code-proposal.json'),'state_sha256':digest(directory/'contract-state.json')}
        service=CandidateTools(agent,baseline,root/'host/tools',variant_name=variant,deadline=time.monotonic()+60)
        return service,request

    def test_immutable_request_cache_and_compile_only_feedback_boundary(self):
        for variant in ('full','compile_only_repair'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory(dir=ROOT/'run') as temp:
                service,request=self.fixture(Path(temp),variant);calls=[]
                def prepare(agent,baseline,candidate,proposal):
                    (candidate/'output/native/run-process').mkdir(parents=True)
                    (candidate/'output/native/run-process/stdout.jsonl').write_text('{"phase":"case_begin","case":"synthetic"}\n{"phase":"case_end"}\n')
                    return {'revision':proposal['revision']}
                def native(*args,**kwargs):
                    calls.append('native');return {'status':'observed','checks':{'status':'violated',
                        'upstream_findings':[{'object':'dbc','subject':'quantities.x','status':'unknown',
                            'check':'executable_physical_relation','detail':'synthetic declaration missing'}]}}
                with patch('tests.common.autodriver_tools.prepare_candidate',side_effect=prepare) as prepared,\
                     patch('tests.common.autodriver_tools.public_compile',return_value={'status':'pass','commands':[]}),\
                     patch('tests.common.autodriver_tools.native_nuttx',side_effect=native):
                    response=service.check(request);self.assertEqual(service.check(request),response);self.assertEqual(prepared.call_count,1)
                    if variant=='full':
                        self.assertEqual(response['repair_diagnostics'][0]['producer'],'method_trace_checker')
                        self.assertEqual(response['upstream_diagnostics'][0]['findings'][0]['object'],'dbc')
                    else:
                        self.assertEqual(response['repair_diagnostics'],[]);self.assertNotIn('native_checks_status',response)
                        self.assertNotIn('upstream_diagnostics',response)
                    changed=dict(request,state_sha256='0'*64)
                    with self.assertRaises(ValueError):service.check(changed)
                self.assertEqual(calls,['native'])

    def test_arbitrary_paths_commands_input_drift_and_links_are_refused_before_tools(self):
        for change in ('command','hash','closed','link','deadline'):
            with self.subTest(change=change),tempfile.TemporaryDirectory(dir=ROOT/'run') as temp:
                service,request=self.fixture(Path(temp));directory=service.agent/'output/autodriver'
                if change=='command':request['command']='forbidden'
                elif change=='hash':request['proposal_sha256']='0'*64
                elif change=='closed':
                    proposal=json.loads((directory/'code-proposal.json').read_text());proposal['full_scope_gate']='closed';save(directory/'code-proposal.json',proposal)
                    request['proposal_sha256']=digest(directory/'code-proposal.json')
                elif change=='link':
                    path=directory/'code-proposal.json';path.unlink();path.symlink_to(ROOT/'README.md')
                else:service.deadline=time.monotonic()-1
                with patch('tests.common.autodriver_tools.prepare_candidate') as prepare:
                    with self.assertRaises((ValueError,TimeoutError)):service.check(request)
                    prepare.assert_not_called()
