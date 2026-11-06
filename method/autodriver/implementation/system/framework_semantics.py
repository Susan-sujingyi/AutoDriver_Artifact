"""Stage-specific semantic recovery duties; no device facts or admission gate.

These instructions describe what an actionable framework fact and binding mean.
They prescribe neither a serialized contract format nor an OS/device answer.
"""

FRAMEWORK_SEMANTICS = """For each relevant actual API, recover:
- Its receiver/provider and configuration, exact parameter types and valid
  values, lengths, flags, enumeration members and required object fields.
  A type or upper range bound alone need not describe a valid value: inspect
  sentinel entries, reserved values and metadata-dependent constraints in the
  public implementation. Do not guess missing constants or fields.
- Each calling precondition and how it can be established. Identify the actual
  setup/registration/configuration entry or field initialization, its receiver,
  required inputs, successful effect and relevant failure/release effect.
  Distinguish a requirement from an action that establishes it. Record when the
  condition starts to hold, how long it holds and what invalidates it.
- Which actor the framework requires to establish each condition: driver,
  public integration, caller or an explicitly stated environment assumption.
  Framework-level duties describe roles, not a current device binding. An API
  declaration or a predicate name does not establish that a caller fulfilled it.
- Acceptance, synchronous error, successful completion and completion error,
  with object/buffer/request identity, ownership and partial effects. State
  successful postconditions conditionally on the corresponding success event.
Follow implementation calls, referenced data tables, type/enum declarations and
provider lifecycle methods needed to answer these questions. The supplied scope
is a mechanical retrieval audit; recover facts from approved originals. Separate
source-level facts from provider/hardware assurance. Keep genuine gaps explicit.
Use the existing FP API/lifecycle/resource entries or readable descriptions;
there is no required constructor syntax or new root field.
"""

BINDING_SEMANTICS = """Make each target framework requirement operational in
the one binding model. For each relevant native call, explain:
  condition -> responsible actor -> triggering entry/event and guard -> actual
  action on the exact receiver/instance -> argument/field values and their
  source -> acceptance/success event -> resulting state -> failure/cleanup.
These are semantic questions, not required JSON keys or a prescribed table.

Map every consumed FP precondition to one of: an actual driver action; a public
integration guarantee with original evidence and scope; an explicit conditional
environment assumption; or an unresolved gap. Never silently assign a duty to
the caller/integration or turn an unresolved condition into a guarantee. A
check of a condition does not perform the operation that establishes it.
Place driver-owned establishment actions before the dependent call on every
applicable path. Show acquisition of resources and initialization of all fields
consumed by native registration/configuration, including per-instance selectors,
valid metadata and object lifetime. Derive actual values from approved framework
and public integration facts, not from an assumed zero/default or an API name.

Show state before submission, after rejection, while accepted, after matching
successful completion and after failure. Only the success event may establish
the promised ready/postcondition; acceptance alone cannot. Relate the exact
object/request/buffer to its callback. Describe partial-setup rollback and when
shutdown/reentry invalidate readiness or require the actions again.
Cover every required public entry/channel, its ownership and native output ABI.
Reference DBC device operations and FP guarantees rather than redefining them.
Use approved target framework retrieval when the FP lacks an establishing API
or a valid argument/field domain; report the gap for directed FP repair instead
of inventing a framework fact. Preserve unresolved items without blocking code.
"""

CODE_SEMANTICS = """Implement the binding as executable actions, not comments
or named readiness predicates. For each relevant native call, trace the path
from the public entry to the operations that establish its preconditions on
the same instance. Honor native parameter/selector domains and initialize every
required object field before use. Do not rely on allocation defaults unless
approved framework evidence establishes that they are valid.
Advance state only after the bound success event. Preserve error/rollback,
buffer ownership and readiness invalidation on failure, shutdown and reentry.
If the BP is absent, carry out this mapping directly from readonly FP, DBC and
public integration. If an establishing API/domain is missing, retrieve approved
target framework originals or preserve the exact unsupported obligation; do not
invent an API, assume integration performed it, or claim a passed check.
"""


def semantic_focus(kind, *, view_only=False):
    if view_only:
        return ('Preserve the readonly primary document, including any stated '
                'actor/action/argument/success/state relations and gaps. '
                'Do not recover or add missing framework or binding semantics.')
    return {'fp': FRAMEWORK_SEMANTICS, 'bp': BINDING_SEMANTICS,
            'code': CODE_SEMANTICS}.get(kind, '')
