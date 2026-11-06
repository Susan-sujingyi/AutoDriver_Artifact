"""Executable physical lower bounds with explicit event and provider premises."""
from dataclasses import dataclass
from fractions import Fraction

from .data import integer
from .expression import seconds
from .model import ContractError, Unknown


@dataclass(frozen=True)
class Event:
    operation: str
    phase: str
    instance: str
    time: Fraction

    def __post_init__(self):
        if not self.operation or not self.instance:
            raise ContractError("event requires operation and instance identity")
        object.__setattr__(self, "time", Fraction(self.time))


def wait_elapsed(anchor, next_event, minimum, maximum=None):
    if anchor.instance != next_event.instance:
        raise ContractError("old operation event cannot satisfy current wait")
    if anchor.phase not in {"physical_start", "physical_success", "logical_success", "commit"}:
        raise ContractError("DBC wait must use its declared physical/logical event")
    lower = seconds(minimum)
    elapsed = next_event.time-anchor.time
    if elapsed < lower:
        raise ContractError("physical minimum wait violated")
    if maximum is not None and elapsed > seconds(maximum):
        raise ContractError("physical maximum wait violated")
    return elapsed


def required_ticks(duration, frequency_max, phase_loss, *, minimum_ticks, maximum_ticks):
    if frequency_max is None or phase_loss is None:
        raise Unknown("frequency bound and maximum phase loss require provider evidence")
    frequency_max, phase_loss = Fraction(frequency_max), Fraction(phase_loss)
    if frequency_max <= 0 or phase_loss < 0:
        raise ContractError("invalid clock bound")
    ideal = seconds(duration)*frequency_max + phase_loss
    ticks = max(integer(minimum_ticks), -((-ideal.numerator)//ideal.denominator))
    if ticks > integer(maximum_ticks):
        raise ContractError("wait cannot fit native counter; saturation is unsafe")
    return ticks


def check_tick_choice(duration, ticks, frequency_max, phase_loss, *, minimum_ticks, maximum_ticks,
                      reference_not_before_anchor, callback_not_before_deadline):
    if reference_not_before_anchor is not True or callback_not_before_deadline is not True:
        raise Unknown("reference event or native callback ordering is not established")
    required = required_ticks(duration, frequency_max, phase_loss,
                              minimum_ticks=minimum_ticks, maximum_ticks=maximum_ticks)
    integer(ticks, minimum_ticks, maximum_ticks)
    if ticks < required:
        raise ContractError("tick choice permits an early physical completion")
    return {"status": "satisfied", "minimum_ticks": required,
            "physical_lower_seconds": str((Fraction(ticks)-Fraction(phase_loss))/Fraction(frequency_max)),
            "scope": "declared frequency, phase, reference and callback bounds; no upper-bound/liveness proof"}


def check_parametric_tick_choice(duration,ticks,frequency_max,phase_loss,*,minimum_ticks,maximum_ticks,
                                variables,assuming=(),reference_not_before_anchor,callback_not_before_deadline):
    """Conditional arithmetic over declared parameters, never a provider proof.

    Guards are retained in the result. A caller must separately verify their
    source and implementation; a satisfiable guarded formula is not admission
    to a different device policy or a claim about an actual clock instance.
    """
    from .domains import symbolic_domain
    from .solver import prove
    from .model import plain
    if reference_not_before_anchor is not True or callback_not_before_deadline is not True:
        raise Unknown('reference event or native callback ordering is not established')
    if not isinstance(assuming,(tuple,list)) or len(assuming)>32 or any(not isinstance(x,str) for x in assuming):
        raise ContractError('bounded explicit clock preconditions required')
    symbols,premises=symbolic_domain(variables)
    def expression(value):
        if type(value) in {int,Fraction}:
            value=Fraction(value);return '('+str(value.numerator)+'/'+str(value.denominator)+')'
        if isinstance(value,str) and 0<len(value)<=2048:return '('+value+')'
        raise Unknown('pure scalar clock expression required')
    n,f,phi,lo,hi=map(expression,(ticks,frequency_max,phase_loss,minimum_ticks,maximum_ticks))
    d=expression(seconds(duration))
    conditions=f'{f}>0 and {phi}>=0 and {lo}>=0 and {hi}>={lo} and {n}>={lo} and {n}<={hi} and ({n}//1)=={n} and {n}-{phi}>={d}*{f}'
    result=prove(conditions,symbols,[*premises,*assuming])
    return {**result,'conditional':True,'variables':plain(variables),'assuming':list(assuming),'predicate':conditions,
        'provider_assurance':'unknown','candidate_guard_conformance':'not_checked',
        'scope':'only the declared parametric lower-bound/range/integer arithmetic; premises, runtime guards, actual clock, physical provider, upper bounds and liveness are not proved'}


def callback_anchor(physical, callback, *, provider_relation):
    if physical.operation != callback.operation or physical.instance != callback.instance:
        raise ContractError("callback belongs to another operation")
    if physical.phase != "physical_success" or callback.phase != "callback_success":
        raise ContractError("successful physical/callback pair required")
    if provider_relation not in {"equal", "device_not_after_native"}:
        raise Unknown("provider physical completion relation not established")
    if physical.time > callback.time or (provider_relation == "equal" and physical.time != callback.time):
        raise ContractError("callback contradicts provider completion relation")
    return callback
