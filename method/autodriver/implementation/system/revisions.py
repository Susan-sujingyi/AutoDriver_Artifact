"""Append-only parent revisions and deterministic downstream invalidation."""
from dataclasses import dataclass
from pathlib import Path
import json

from .protocol.assembler import canonical,digest,write_private_json
from .contract.model import ContractError


@dataclass(frozen=True)
class Revision:
    kind: str
    identity: str
    artifact_json: str
    parents_json: str

    @property
    def artifact(self):return json.loads(self.artifact_json)

    @property
    def parents(self):return json.loads(self.parents_json)


class Revisions:
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.records={};self.current={};self.invalidated=set();self.frozen=None

    def add(self,kind,artifact,parents,*,provenance=None):
        if self.frozen is not None:raise ContractError('final candidate is frozen; no repair or parent change is allowed')
        if kind not in {'source_fp','target_fp','dbc','bp','code'}:raise ContractError('unknown revision object')
        if artifact['kind']!=('fp' if kind.endswith('_fp') else kind):raise ContractError('repair changes the wrong object')
        if any(self.current.get(key)!=value or value in self.invalidated for key,value in parents.items()):
            raise ContractError('downstream revision uses a missing or superseded parent')
        record={'schema':'autodriver.contract-revision/v1','kind':kind,'artifact':artifact,'parents':dict(parents)}
        if provenance is not None:record['provenance']=provenance
        sha=digest(canonical(record).encode())
        previous=self.current.get(kind)
        if previous is not None and previous!=sha:
            self.invalidated.add(previous)
            changed=True
            while changed:
                changed=False
                for identity,revision in self.records.items():
                    if identity not in self.invalidated and set(revision.parents.values()) & self.invalidated:
                        self.invalidated.add(identity);changed=True
            self.current={k:v for k,v in self.current.items() if v not in self.invalidated}
        self.records[sha]=Revision(kind,sha,canonical(artifact),canonical(parents));self.current[kind]=sha
        path=self.directory/(sha+'.json')
        if not path.exists():write_private_json(path,record)
        write_private_json(self.directory/'index.json',{'current':self.current,'invalidated':sorted(self.invalidated)})
        return self.records[sha]

    def freeze(self,files):
        if self.frozen is not None:raise ContractError('candidate already frozen')
        if 'code' not in self.current or self.current['code'] in self.invalidated:
            raise ContractError('no current final candidate to freeze')
        if not files:raise ContractError('candidate freeze needs a nonempty canonical source inventory')
        value={'schema':'autodriver.final-candidate/v1','revisions':dict(self.current),'files':dict(files)}
        self.frozen=digest(canonical(value).encode())
        write_private_json(self.directory/'final-candidate.json',dict(value,identity=self.frozen))
        return self.frozen
