"""Finite host service for public candidate builds and method-native checks.

The isolated method supplies hashes of fixed output files, never a command,
host path or evaluation selector. Candidates are changed only under run/host.
This service has no independent-scoring imports or execution capability.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from benchmark.common.api import ROOT,save,digest
from tests.common.autodriver_json import load
from method.autodriver.implementation.system.protocol.assembler import digest as sha,canonical
from method.autodriver.implementation.system.variants import variant
from tests.common.autodriver_candidate import prepare_candidate,public_compile,host_path
from tests.common.autodriver_native import native_tock
from tests.common.autodriver_nuttx_native import native_nuttx
from tests.common.execution.artifacts import read_regular


class CandidateTools:
    def __init__(self,agent,baseline,directory,*,variant_name,deadline,development_partial=False,
                 admission_policy='strict_contract_checks'):
        self.agent=Path(agent).resolve(strict=True);self.baseline=host_path(baseline);self.directory=host_path(directory)
        if not self.agent.is_relative_to(ROOT/'run'):raise ValueError('prepared package-owned agent required')
        self.policy=variant(variant_name);self.variant_name=variant_name;self.deadline=deadline
        self.development_partial=development_partial;self.lock=threading.Lock();self.records={};self.last_candidate=None
        if admission_policy not in {'strict_contract_checks','advisory_contract_checks'}:
            raise ValueError('unknown host candidate admission policy')
        if admission_policy=='advisory_contract_checks' and development_partial:
            raise ValueError('advisory formal policy is separate from legacy partial development')
        self.admission_policy=admission_policy
        self.frozen=None
        self.directory.mkdir(parents=True,exist_ok=False)

    def fixed_output(self,name,expected):
        path=self.agent/'output/autodriver'/name
        if any(p.is_symlink() for p in (self.agent/'output',path.parent,path)):raise ValueError('linked method output refused')
        data=read_regular(path)
        if len(data)>8_000_000 or sha(data)!=expected:raise ValueError('candidate tool input identity/size changed')
        return json.loads(data)

    def check(self,request):
        required={'schema','revision','proposal_sha256','state_sha256'}
        if (not isinstance(request,dict) or set(request)!=required or request['schema']!='autodriver.candidate-check-request/v1'
                or any(not isinstance(request[k],str) or len(request[k])!=64 or any(c not in '0123456789abcdef' for c in request[k]) for k in required-{'schema'})):
            raise ValueError('finite candidate tool request required')
        with self.lock:
            if self.frozen is not None:raise ValueError('final candidate is frozen; no further tool or repair allowed')
            revision=request['revision']
            if revision in self.records:
                prior=self.records[revision]
                if prior['request']!=request:raise ValueError('candidate revision was reused with changed inputs')
                return prior['response']
            if len(self.records)>=5:raise ValueError('five candidate build/check attempts exhausted')
            if time.monotonic()>=self.deadline:raise TimeoutError('shared trial deadline reached')
            proposal=self.fixed_output('code-proposal.json',request['proposal_sha256'])
            state=self.fixed_output('contract-state.json',request['state_sha256'])
            task=load(self.agent/'task.yaml');manifest=load(self.agent/'materials.json')
            if (proposal.get('revision')!=revision or proposal.get('applied') is not False
                    or state.get('task_id')!=task['task_id'] or state.get('input_identity')!=manifest['identity']
                    or state.get('variant')!=self.variant_name):raise ValueError('candidate task/input/variant identity mismatch')
            if self.admission_policy=='advisory_contract_checks':
                from tests.common.autodriver_document_state import load_document_state
                state=load_document_state(self.agent,state,proposal,task,manifest,variant_name=self.variant_name)
            else:
                if state.get('schema')!='autodriver.contract-state/v1':raise ValueError('legacy candidate state schema required')
                if not self.development_partial and proposal.get('full_scope_gate')!='open':raise ValueError('normal candidate checks require admitted upstream obligations')
                materials={entry['id']:entry for entry in state['materials']}
                for entry in materials.values():
                    if 'original_path' in entry:
                        path=entry['original_path']
                        if path not in manifest['files'] or manifest['files'][path]['sha256']!=entry['sha256']:raise ValueError('unapproved original in candidate state')
                    elif sha(entry['text'].encode())!=entry['sha256']:raise ValueError('changed derived contract state')
                code=materials[state['objects']['code']['material_id']]
                if json.loads(code['text'])!=proposal['artifact']:raise ValueError('candidate proposal differs from current code revision')
            number=len(self.records)+1;candidate=self.directory/('candidate-'+str(number))
            record=prepare_candidate(self.agent,self.baseline,candidate,proposal);self.last_candidate=candidate
            # Reserve this attempt before running a tool. An incomplete build
            # cannot reopen a fresh fifth attempt or select an older source.
            stored={'request':dict(request),'response':{'schema':'autodriver.candidate-check/v1','revision':revision,'attempt':number,
                'applied_revision':record['revision'],
                'status':'not_completed','cleanup_status':'unknown','repair_diagnostics':[],'independent_scoring':'not_run','hardware_status':'not_run'}}
            self.records[revision]=stored;response=stored['response'];save(candidate/'tool-request.json',request)
            processes=[]
            try:
                compile_report=public_compile(candidate,candidate/'output/compile-process',timeout=min(600,max(0.01,self.deadline-time.monotonic())))
                processes.append(compile_report.get('execution',{}))
                response['compile_status']=compile_report['status']
                response['status']='completed'
                if compile_report['status']=='fail':
                    commands=[item for item in compile_report['commands'] if item.get('returncode') not in {None,0} and not item.get('timed_out')]
                    if commands and len(canonical(commands).encode())<=196608:
                        response['repair_diagnostics']=[{'schema':'autodriver.internal-candidate-diagnostic/v1','producer':'approved_compile_tool',
                            'candidate_revision':record['revision'],'commands':commands,'scope':'complete failing public compiler commands only'}]
                    else:response['status']='diagnostic_unavailable'
                elif compile_report['status']=='pass' and time.monotonic()<self.deadline:
                    executor=native_tock if task['target']['os']=='tock' else native_nuttx
                    if self.admission_policy=='advisory_contract_checks':
                        from tests.common.autodriver_document_native import document_native
                        native=document_native(executor,candidate,candidate/'output/native',state,
                            deadline=self.deadline,binding_plan=self.policy.binding_plan,
                            framework_executor=lambda c,d,s,**kw:executor(c,d,s,framework_only=True,**kw))
                    else:native=executor(candidate,candidate/'output/native',state,deadline=self.deadline)
                    processes.extend(native[key] for key in ('build','execution') if key in native)
                    processes.extend(native.get('executions',[]))
                    # Compile-only may record checks on the host but gets no
                    # candidate-dependent result or repair signal from them.
                    if self.policy.candidate_contract_repair:
                        response['native_execution_status']=native['status']
                        checks=native.get('checks',{});response['native_checks_status']=checks.get('status','unknown')
                        if native.get('status')=='observed' and checks.get('upstream_findings'):
                            response['upstream_diagnostics']=[{'schema':'autodriver.upstream-candidate-diagnostic/v1',
                                'producer':'method_trace_checker','candidate_revision':record['revision'],
                                'findings':checks['upstream_findings'],
                                'scope':'typed method checker prerequisite gaps; diagnostic text is not source evidence'}]
                        if native.get('status')=='observed' and checks.get('status')=='violated':
                            from method.autodriver.implementation.system.native.plan import split_cases
                            observation=candidate/'output/native'/native.get('observation_path','run-process/stdout.jsonl')
                            if not observation.resolve().is_relative_to(candidate/'output/native'):
                                raise ValueError('native observation path escaped candidate evidence')
                            cases=split_cases(observation.read_text())
                            response['repair_diagnostics']=[{'schema':'autodriver.internal-candidate-diagnostic/v1','producer':'method_trace_checker',
                                'candidate_revision':record['revision'],'checks':checks,'counterexamples':[{'case':name,'observations':events} for name,events in cases.items()],
                                'contract_view':native.get('projection',{'primary_document_entailment':'unverified'}),
                                'scope':'actual public-native candidate observations against current contracts; finite cases only; no scoring or hardware'}]
                return response
            finally:
                response['cleanup_status']='pass' if processes and all(p.get('cleanup',{}).get('status')=='pass' for p in processes) else 'unknown'
                save(candidate/'tool-response.json',response)
                save(self.directory/'index.json',{'schema':'autodriver.candidate-tools/v1','variant':self.variant_name,
                    'admission_policy':self.admission_policy,
                    'attempts':list(self.records.values()),'last_candidate':str(self.last_candidate.relative_to(ROOT)),
                    'independent_scoring':'not_run','hardware_status':'not_run'})

    def freeze_final(self,destination,*,expected_revision=None):
        """Freeze the last applied source after method and tool processes stop."""
        with self.lock:
            if self.frozen is not None:raise ValueError('final candidate already frozen')
            if self.last_candidate is None:return None
            if any(item['response'].get('cleanup_status')!='pass' for item in self.records.values()):
                raise ValueError('candidate tool cleanup not verified')
            record=load(self.last_candidate/'candidate.json');files=record['changes']
            if expected_revision is not None and record['revision']!=expected_revision:
                raise ValueError('final proposed/applied revisions differ; an older candidate cannot be substituted')
            for path,expected in files.items():
                actual=self.last_candidate/path
                if expected is None:
                    if actual.exists() or actual.is_symlink():raise ValueError('final candidate delete drifted')
                elif sha(read_regular(actual))!=expected:raise ValueError('final candidate bytes changed after checks')
            submission=self.last_candidate/'output/submission'
            value={'schema':'autodriver.frozen-candidate/v1','candidate_revision':record['revision'],'files':files,
                'submission_manifest_sha256':digest(submission/'manifest.json'),'patch_sha256':digest(submission/'driver.patch'),
                'last_candidate':str(self.last_candidate.relative_to(ROOT)),'attempts':len(self.records),
                'method_variant':self.variant_name,'independent_scoring':'not_run','hardware_status':'not_run'}
            self.frozen=sha(canonical(value).encode());value['identity']=self.frozen;save(destination,value)
            return value

    def archive(self,destination):
        """Persist sources and public-tool evidence, excluding build trees."""
        destination=Path(destination);destination.mkdir(parents=True,exist_ok=False);hashes={}
        def copy(source,relative):
            data=read_regular(source);target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            hashes[str(relative)]=sha(data)
        for number in range(1,len(self.records)+1):
            candidate=self.directory/('candidate-'+str(number));prefix=Path('candidate-'+str(number))
            for name in ('candidate.json','tool-request.json','tool-response.json'):
                if (candidate/name).is_file():copy(candidate/name,prefix/name)
            if (candidate/'candidate.json').is_file():
                for path,expected in load(candidate/'candidate.json')['changes'].items():
                    if expected is not None:copy(candidate/path,prefix/'source'/path)
            for name in ('compile-process','native/build-process','native/run-process'):
                directory=candidate/'output'/name
                if directory.is_dir() and not directory.is_symlink():
                    for path in sorted(directory.iterdir()):
                        if path.is_file() and path.suffix in {'.json','.jsonl','.log'}:copy(path,prefix/name/path.name)
            public=candidate/'output/public-compile'
            if public.is_dir():
                for path in sorted(public.iterdir()):
                    if path.is_file() and (path.name=='report.json' or path.name.startswith('step-') and path.suffix=='.log'):
                        copy(path,prefix/'public-compile'/path.name)
            native=candidate/'output/native'
            if (native/'native-report.json').is_file():copy(native/'native-report.json',prefix/'native-report.json')
            for name in ('crate/src','staging'):
                directory=native/name
                if directory.is_dir() and not directory.is_symlink():
                    for path in sorted(directory.rglob('*')):
                        if path.is_file() and not path.is_symlink() and path.suffix in {'.rs','.c','.h'}:
                            copy(path,prefix/'native-source'/path.relative_to(directory))
            if native.is_dir():
                for directory in sorted(native.glob('case-*')):
                    if directory.is_dir() and not directory.is_symlink():
                        for path in sorted(directory.iterdir()):
                            if path.is_file() and path.suffix in {'.json','.jsonl','.log'}:copy(path,prefix/directory.name/path.name)
            probe=native/'framework-probe'
            if probe.is_dir():
                for name in ('native-report.json',):
                    if (probe/name).is_file():copy(probe/name,prefix/'framework-probe'/name)
                for directory in [probe/'build-process',probe/'run-process',*sorted(probe.glob('case-*'))]:
                    if directory.is_dir():
                        for path in sorted(directory.iterdir()):
                            if path.is_file() and path.suffix in {'.json','.jsonl','.log'}:
                                copy(path,prefix/'framework-probe'/directory.name/path.name)
                for name in ('crate/src','staging'):
                    directory=probe/name
                    if directory.is_dir():
                        for path in sorted(directory.rglob('*')):
                            if path.is_file() and not path.is_symlink() and path.suffix in {'.rs','.c','.h'}:
                                copy(path,prefix/'framework-probe-source'/path.relative_to(directory))
        if (self.directory/'index.json').is_file():copy(self.directory/'index.json',Path('index.json'))
        save(destination/'archive.json',{'schema':'autodriver.candidate-tool-archive/v1','files':hashes,
            'attempts':len(self.records),'scope':'public generated source, compiler logs and method-native finite observations only',
            'independent_scoring':'not_run','hardware_status':'not_run'})
