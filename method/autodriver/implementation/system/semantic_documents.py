"""Primary semantic documents and advisory observations, owned by the host.

No model-authored field grants authority, proves a claim or opens a tool. Raw
documents may guide generation while their unsupported/unproved parts remain
visible. Input access, tool schemas, budgets and code paths are enforced by
their existing independent boundaries.
"""
from dataclasses import dataclass, asdict
import hashlib
import json


SEMANTIC_GUIDE = """A contract records meanings; no constructor syntax is required.
Use readable prose, tables, pseudocode or structured data. Keep one primary
model; a graph or executable projection is a derived view, never another
normative specification. State genuine gaps and conflicts explicitly.

1. Authority: distinguish device requirements, frozen product policy, explicitly
preserved acquisition behavior, framework guarantees, implementation choices
and environmental assumptions. A source implementation habit is not implicitly
a required policy. A citation or assertion is not proof of original support.
2. Control: describe initialization, acquisition, failure, recovery and reentry
in one global model. Identify triggering events, guards, I/O, data updates and
request outcomes. No recovery policy does not imply automatic retry or ready.
3. Events/time: distinguish submission, immediate acceptance/rejection, physical
start/end and callback. Name the current operation/request and timing anchors,
units, lower bounds and conditions. An old callback cannot complete new work.
4. Data lifetime: distinguish current complete frames, committed samples and
cached values. Calibration may belong to a device/configuration epoch; do not
mistake an initialization frame for a current acquisition frame. State validity,
invalidation and empty-cache behavior. Old data cannot masquerade as new success.
5. Numeric meaning: preserve one normative physical relation and its raw/bit
layout and named intermediate values. Separate input domain, output range,
source finite-precision approximation, target encoding and error. Unknown
domains, overflow or precision remain explicit, never guessed true.
6. Framework: record relevant actual APIs/providers, valid argument/field domains,
calling preconditions and the real actions/roles that establish and invalidate
them, acceptance, completion, errors, buffer/resource ownership, clocks and output ABI.
Framework facts contain no device commands, frames or compensation formulas.
7. Binding: map semantic device operations/events/data to actual native calls,
callbacks, state, resources, timing conversion and request outcomes. For each
precondition identify who acts, when, on which instance, with which valid inputs,
and which success event establishes the next state; retain failure/cleanup.
Do not
redefine device facts or API guarantees, or pretend an absent mapping is done.
8. Progress: record environmental assumptions for eventual progress. Finite
executions do not prove universal liveness. Preserve conditional/unproved
obligations as such; they are advisory, not a reason to suppress generation.

Available check results remain observations with their exact scope. A parser
failure, unsupported projection or missing proof is never a passed check.
Generate a best-effort implementation from actual drafts and approved originals;
do not invent evidence, device strategy, successful checks or hidden guarantees.
"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class ParentIdentity:
    role: str
    material_id: str
    sha256: str

    def __post_init__(self):
        if self.role not in {'source_fp', 'target_fp', 'dbc', 'bp', 'original'}:
            raise ValueError('unknown host parent role')
        if not self.material_id or any(c not in '0123456789abcdef' for c in self.sha256) or len(self.sha256) != 64:
            raise ValueError('exact host material identity required')


@dataclass(frozen=True)
class SemanticDocument:
    kind: str
    task_id: str
    call_id: str
    stage: str
    raw_text: str
    parents: tuple
    os_role: str = 'shared'
    response_role: str = 'draft'

    def __post_init__(self):
        if self.kind not in {'fp', 'dbc', 'bp'} or self.stage not in {'fp_build', 'dbc_recover', 'bp_build', 'repair'}:
            raise ValueError('host contract stage and kind required')
        if self.stage != 'repair' and {'fp_build':'fp', 'dbc_recover':'dbc', 'bp_build':'bp'}[self.stage] != self.kind:
            raise ValueError('host stage does not produce this document kind')
        if (self.os_role not in {'source','target','shared'}
                or self.kind == 'fp' and self.os_role == 'shared'
                or self.kind == 'dbc' and self.os_role != 'shared'
                or self.kind == 'bp' and self.os_role != 'target'):
            raise ValueError('explicit source/target framework role and device/binding scope required')
        if not self.task_id or not self.call_id or not isinstance(self.raw_text, str):
            raise ValueError('host call identity and original response text required')
        if not isinstance(self.parents, tuple) or any(not isinstance(p, ParentIdentity) for p in self.parents):
            raise ValueError('immutable host parent identities required')
        if self.response_role not in {'draft','evidence_only','declared_blocked'}:
            raise ValueError('known captured response role required')
        if len(self.raw_text.encode()) > 2_000_000:
            raise ValueError('bounded raw document size exceeded')

    @property
    def raw_sha256(self):
        return sha256(self.raw_text)

    @property
    def identity(self):
        return sha256(canonical(dict(kind=self.kind, task_id=self.task_id, call_id=self.call_id,
                                    stage=self.stage, os_role=self.os_role, response_role=self.response_role,
                                    raw_sha256=self.raw_sha256,
                                    parents=[asdict(p) for p in self.parents])))

    def record(self):
        return dict(schema='autodriver.semantic-document/v1', identity=self.identity,
                    kind=self.kind, task_id=self.task_id, call_id=self.call_id, stage=self.stage,
                    os_role=self.os_role,
                    response_role=self.response_role,
                    raw_text=self.raw_text, raw_sha256=self.raw_sha256,
                    parents=[asdict(p) for p in self.parents],
                    availability=self.availability,
                    evidence_entailment='unverified', executable_projection='not_attempted',
                    assurance='not_asserted')

    @property
    def availability(self):
        if not self.raw_text.strip():
            return 'empty_response'
        return 'present' if self.response_role=='draft' else 'draft_not_produced'


def advisory_generation_record(documents, observations, *, expected_contracts=('dbc','bp')):
    """Record intermediate diagnostics without turning them into admission.

    This function has no permissions, tool execution or model budget inputs;
    those independent boundaries must already be enforced by the caller.
    Model-authored status fields inside raw_text are never read as checks.
    """
    if any(not isinstance(d, SemanticDocument) for d in documents):
        raise ValueError('host-captured semantic documents required')
    if set(expected_contracts) - {'source_fp','target_fp','dbc','bp'}:
        raise ValueError('unknown expected contract kind')
    for observation in observations:
        if (not isinstance(observation, dict) or observation.get('status') not in
                {'satisfied', 'unknown', 'violated', 'unsupported', 'not_checked'}):
            raise ValueError('explicit checker observation status required')
    # JSON round-trip copies the observations without changing their statuses.
    preserved = json.loads(canonical(observations))
    return dict(schema='autodriver.advisory-generation/v1', generation_allowed=True,
                policy='advisory_contract_checks', contract_assurance='not_asserted',
                documents=[dict(kind=d.kind, os_role=d.os_role, identity=d.identity, raw_sha256=d.raw_sha256,
                                availability=d.availability) for d in documents],
                observations=preserved, expected_contracts=list(expected_contracts),
                missing_contracts=sorted(set(expected_contracts)-{
                    d.os_role+'_fp' if d.kind=='fp' else d.kind
                    for d in documents if d.availability=='present'}),
                scope='contract diagnostics only; no material/tool/path/budget authority or candidate correctness claim')
