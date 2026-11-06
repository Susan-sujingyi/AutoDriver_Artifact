"""One derived transition graph for compositional and explicit device behavior.

The interpreter checks supplied contract traces. It never claims that these are
traces of a native candidate, nor that finite execution establishes liveness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import defaultdict
from fractions import Fraction

from .model import Node, ContractError, Unknown, unwrap
from .expression import evaluate, Quantity, seconds
from .data import integer, decode_frame, commit, byte_sequence, DataInvalid,frame_environment
from .timing import Event, wait_elapsed


IO = {"WriteBytes", "Transfer", "WriteRegister", "ReadRegister", "UpdateRegister", "ReadFrame", "DriveSignal", "SampleSignal"}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    event: object = None
    guard: object = True
    action: object = None
    effects: tuple = ()
    path: str = ""
    failure: str | None = None


@dataclass
class Graph:
    initial: str
    accepting: str
    failing: str
    unknown: str
    edges: list = field(default_factory=list)
    variables: dict = field(default_factory=dict)
    variable_types: dict = field(default_factory=dict)
    invariants: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)

    def reachable(self):
        adjacent = defaultdict(set)
        for edge in self.edges:
            if edge.guard is not False:
                adjacent[edge.source].add(edge.target)
        result, pending = set(), [self.initial]
        while pending:
            value = pending.pop()
            if value not in result:
                result.add(value); pending.extend(adjacent[value] - result)
        return result


def lower(behavior, *, maximum_states=20000):
    graph = Graph("entry", "success", "failure", "unknown")
    serial = 0

    def fresh(path):
        nonlocal serial
        serial += 1
        if serial > maximum_states:
            raise Unknown("FSM expansion bound exceeded")
        return f"{path}#{serial}"

    def edge(source, target, **values):
        graph.edges.append(Edge(source, target, **values))

    def declare_variables(declarations):
        if not hasattr(declarations,'items'):raise ContractError('variables require a name/type declaration map')
        for key,declaration in declarations.items():
            if key in graph.variables or not isinstance(declaration,dict) and not hasattr(declaration,'keys'):
                raise ContractError('variable needs a unique type/initial declaration')
            if set(declaration)!={'type','initial'} or declaration['type'] not in {'bool','int','real'}:
                raise Unknown('unsupported FSM variable declaration')
            initial=declaration['initial'];kind=declaration['type']
            if kind=='bool' and type(initial) is not bool:raise ContractError('boolean FSM initializer required')
            if kind=='int' and type(initial) is not int:raise ContractError('integer FSM initializer required')
            if kind=='real' and type(initial) not in {int,Fraction}:raise ContractError('numeric FSM initializer required')
            graph.variables[key]=initial;graph.variable_types[key]=kind

    def build(value, start, end, fail, path):
        try:
            value = unwrap(value)
        except Unknown as error:
            graph.gaps.append({"path": path, "reason": str(error)})
            edge(start, graph.unknown, path=path)
            return
        if not isinstance(value, Node):
            raise ContractError("behavior must use behavioral constructors")
        name = value.name
        if name == "Sequence":
            current = start
            for i, step in enumerate(value["steps"]):
                after = fresh(path)
                build(step, current, after, fail, f"{path}.steps.{i}"); current = after
            edge(current, end, path=path)
        elif name == "Choice":
            previous = []
            for i, case in enumerate(value["cases"]):
                if case.name != "Case":
                    raise ContractError("Choice requires Case values")
                selected = fresh(path)
                condition = ("and", *( ("not", x) for x in previous), case["guard"])
                edge(start, selected, guard=condition, path=path)
                build(case["body"], selected, end, fail, f"{path}.cases.{i}.body")
                previous.append(case["guard"])
            otherwise = fresh(path)
            edge(start, otherwise, guard=("and", *(("not", x) for x in previous)), path=path)
            build(value["otherwise"], otherwise, end, fail, path+".otherwise")
        elif name == "Repeat":
            count = fresh("repeat_count")
            graph.variables[count] = 0
            graph.variable_types[count] = 'int'
            body_start, body_end = fresh(path), fresh(path)
            edge(start, body_start, guard=("remaining",count,value["maximum_attempts"],value["deadline"]),
                 action=("set", count, 1), path=path)
            edge(start, fail, guard=("not",("remaining",count,value["maximum_attempts"],value["deadline"])),path=path)
            build(value["body"], body_start, body_end, fail, path+".body")
            edge(body_end, end, guard=value["until"], path=path)
            limit = value["maximum_attempts"]
            if limit is not None:
                integer(limit, 1, 1000000)
            else:
                graph.gaps.append({"path": path, "reason": "loop progress is not proved"})
            continuing = ("and", ("not", value["until"]), ("remaining", count, limit, value["deadline"]))
            edge(body_end, body_start, guard=continuing, action=("increment", count), path=path)
            edge(body_end, fail, guard=("and", ("not", value["until"]),
                                      ("not", ("remaining", count, limit, value["deadline"]))), path=path)
        elif name == "WithFailure":
            handled = fresh(path)
            build(value["body"], start, end, handled, path+".body")
            # Successful error handling preserves failure of the original request.
            build(value["on_failure"], handled, graph.failing, fail, path+".on_failure")
        elif name == "Lifecycle":
            declare_variables(value.get('variables',{}))
            init_start, ready, acquire_start, acquired = [fresh(path) for _ in range(4)]
            handler = fresh(path)
            edge(start, init_start, event=("initialize", "logical_success"), path=path)
            build(value["initialization"], init_start, ready, handler, path+".initialization")
            edge(ready, acquire_start, event=("acquire", "logical_success"), action=("new_acquisition",), path=path)
            build(value["acquisition"], acquire_start, acquired, handler, path+".acquisition")
            edge(acquired, end, path=path)
            edge(end, ready, event=("next_request", "logical_success"), path=path)
            build(value["on_failure"], handler, fail, fail, path+".on_failure")
            # No recovery/reentry path is invented. Explicit non-unknown policies
            # require a separate operational binding before execution.
            for key in ("recovery", "reentry"):
                if value[key] is not None:
                    graph.gaps.append({"path": path+"."+key, "reason": "recovery/reentry obligation requires binding"})
        elif name == "FSM":
            names = [state["name"] for state in value["states"]]
            if len(set(names)) != len(names) or value["initial"] not in names:
                raise ContractError("duplicate states or missing initial state")
            mapped = {state: fresh(path+"."+state) for state in names}
            edge(start, mapped[value["initial"]], path=path)
            for state in value["states"]:
                graph.invariants[mapped[state["name"]]] = state["invariant"]
            declare_variables(value['variables'])
            for state in value["accepting"]:
                if state not in mapped: raise ContractError("missing accepting state")
                edge(mapped[state], end, path=path)
            for state in value["failing"]:
                if state not in mapped: raise ContractError("missing failing state")
                edge(mapped[state], fail, path=path)
            for i, transition in enumerate(value["transitions"]):
                if transition.name != "Transition" or transition["source"] not in mapped or transition["target"] not in mapped:
                    raise ContractError("invalid FSM transition state")
                assignments = transition["effects"]
                writes = [x["variable"] for x in assignments]
                if any(x.name != "Assign" for x in assignments) or len(set(writes)) != len(writes) or set(writes)-graph.variables.keys():
                    raise ContractError("undefined or duplicate parallel assignment")
                event = transition["event"]
                if isinstance(event, Node) and event.name == "EventRef":
                    if event["instance"] != "current":
                        raise ContractError("transition must bind current instance")
                    event = (event["operation"], event["phase"])
                elif isinstance(event, str):
                    event = (event, "logical_success")
                    graph.gaps.append({"path": path, "reason": "external input declaration needs evidence: "+event[0]})
                else:
                    raise ContractError("transition event must be declared")
                after = fresh(path)
                edge(mapped[transition["source"]], after, event=event, guard=transition["guard"], path=path)
                if transition["action"] is not None:
                    complete = fresh(path)
                    build(transition["action"], after, complete, fail, f"{path}.transitions.{i}.action")
                else:
                    complete = after
                edge(complete, mapped[transition["target"]], effects=assignments, path=path)
        elif name=='Assign':
            if value['variable'] not in graph.variable_types:raise ContractError('undefined state assignment')
            edge(start,end,effects=(value,),path=path)
        elif name in IO:
            operation = value.get("name") or path
            pending = fresh(path)
            edge(start, pending, event=(operation, "physical_start"), path=path)
            edge(pending, end, event=(operation, "physical_success"), action=("io_success", value, operation), path=path, failure=fail)
            edge(pending, fail, event=(operation, "physical_failure"), path=path)
        elif name in {"WaitAfterCompletion", "WaitFrom"}:
            edge(start, end, event=(path, "logical_success"), action=("wait", value), path=path)
        elif name == "CommitValidatedSample":
            edge(start, end, action=("commit", value), path=path)
        elif name == "FailCurrentOperation":
            edge(start, fail, action=("fail", value), path=path)
        elif name == "ReadCommittedSample":
            edge(start, end, action=("read_cache", value), path=path)
        else:
            graph.gaps.append({"path": path, "reason": "unsupported behavior primitive: "+name})
            edge(start, graph.unknown, path=path)

    build(behavior, graph.initial, graph.accepting, graph.failing, "behavior")
    return graph


@dataclass(frozen=True)
class InputEvent:
    operation: str
    phase: str
    instance: str
    time: Fraction
    data: object = None


class Interpreter:
    def __init__(self, contract):
        self.contract = contract
        self.graph = lower(contract["behavior"])
        self.state = self.graph.initial
        self.variables = dict(self.graph.variables)
        self.transient_variables = set()
        self.register_widths = {}
        self.frames, self.events = {}, {}
        self.sample = None
        self.sample_variables = None
        self.instance = None
        self.now = Fraction(0)
        self.event = None
        self.steps = 0
        self.acquisitions = set()

    def guard(self, guard):
        if isinstance(guard, tuple):
            op, *args = guard
            if op == "not": return not self.guard(args[0])
            if op == "and": return all(self.guard(x) for x in args)
            if op == "remaining":
                count, limit, deadline = args
                if limit is not None and self.variables[count] >= limit: return False
                if deadline is not None:
                    anchor = self.anchor(deadline["anchor"])
                    if self.now >= anchor.time + seconds(deadline["maximum"]): return False
                return True
            raise Unknown("unsupported derived guard")
        from .expression import bit_guard_width
        from .data import check_layout
        widths=dict(self.register_widths);counts=defaultdict(int)
        for frame in self.frames.values():
            if frame.instance==self.instance:
                for key in frame.fields:counts[key]+=1
        for name,frame in self.frames.items():
            if frame.instance!=self.instance:continue
            for key,field in check_layout(self.contract['frames'][name])['fields'].items():
                widths[name+'.'+key]=field['width']
                if counts[key]==1 and key not in self.variables:widths[key]=field['width']
        value = evaluate(guard, frame_environment(self.frames,self.instance,self.variables,declared_frames=self.contract['frames']),bit_width=bit_guard_width(guard,widths))
        if type(value) is not bool:
            raise ContractError("FSM guard must be boolean")
        return value

    def anchor(self, reference):
        if isinstance(reference, str):
            operation, phase = reference, "physical_success"
        elif isinstance(reference, Node) and reference.name == "EventRef":
            operation, phase = reference["operation"], reference["phase"]
            if reference["instance"] != "current":
                raise ContractError("wait reference must use current instance")
        else:
            raise Unknown("unresolved event anchor")
        event = self.events.get((operation, phase, self.instance))
        if event is None: raise ContractError("required current anchor event has not occurred")
        return event

    def act(self, action):
        if action is None: return
        kind, *args = action
        if kind == "set": self.variables[args[0]] = args[1]
        elif kind == "increment": self.variables[args[0]] += 1
        elif kind == "new_acquisition":
            if not self.event or not self.event.instance: raise ContractError("acquisition identity required")
            self.begin_acquisition(self.event.instance)
        elif kind == "wait":
            wait = args[0]
            anchor = self.anchor(wait["of"] if wait.name == "WaitAfterCompletion" else wait["anchor"])
            next_event = Event(self.event.operation, self.event.phase, self.instance, self.now)
            wait_elapsed(anchor, next_event, unwrap(wait["minimum"]), unwrap(wait["maximum"]))
        elif kind == "io_success":
            operation, name = args
            if operation.name == "ReadFrame":
                frame = operation["frame"]
                if frame not in self.contract["frames"]: raise ContractError("frame is undeclared")
                self.frames[frame] = decode_frame(self.contract["frames"][frame], self.event.data,
                                                 name=frame, instance=self.instance)
            elif operation.name == "ReadRegister":
                data = byte_sequence(self.event.data)
                width = integer(operation["width"], 1, 4096)
                if width % 8 or len(data)*8 != width: raise ContractError("register read width mismatch")
                order = operation["byte_order"]
                if order not in {"big", "little"}: raise ContractError("invalid register byte order")
                name=operation['into']
                if not isinstance(name,str) or not name:raise ContractError('named register output required')
                if self.graph.variable_types.get(name)=='bool':raise ContractError('register integer cannot replace a boolean FSM variable')
                self.variables[name] = int.from_bytes(data, order, signed=operation["signed"])
                self.register_widths[name]=width
                if name not in self.graph.variable_types:self.transient_variables.add(name)
            elif operation.name == "Transfer":
                # Multiple read segments need a segment-indexed event payload.
                reads = [s for s in operation["segments"] if s.name == "ReadSegment"]
                if reads:
                    if not isinstance(self.event.data, dict): raise ContractError("transfer requires named read segments")
                    for read in reads:
                        frame = read["into"]
                        data = byte_sequence(self.event.data.get(frame))
                        if len(data) != read["length"]: raise ContractError("short transfer segment")
                        if frame in self.contract["frames"]:
                            self.frames[frame] = decode_frame(self.contract["frames"][frame], data, name=frame, instance=self.instance)
                        else:
                            raise Unknown("untyped transfer output cannot establish valid data")
            elif operation.name in {"UpdateRegister", "SampleSignal"}:
                raise Unknown("read/modify/write or sampled-signal data semantics not yet encoded")
        elif kind == "commit":
            required = args[0]["from_frame"]
            required = (required,) if isinstance(required, str) else required
            self.sample = commit(self.frames, required, self.instance)
            self.sample_variables = dict(self.variables)
        elif kind == "fail":
            if not args[0]["preserve_committed_sample"]:self.sample=None;self.sample_variables=None
        elif kind == "read_cache":
            if self.sample is None: raise Unknown("empty cached-read behavior needs interpretation")
        else:
            raise Unknown("unsupported derived action")

    def begin_acquisition(self,instance):
        if not instance or instance in self.acquisitions:raise ContractError('acquisition identity cannot be absent or reused')
        self.acquisitions.add(instance);self.instance=instance
        # Only explicit typed FSM state can persist. A successful old register
        # read alone is not a calibration-lifetime or new-sample guarantee.
        for name in self.transient_variables:
            self.variables.pop(name,None);self.register_widths.pop(name,None)
        self.transient_variables.clear()

    def take(self, edge):
        self.steps += 1
        if self.steps > 100000:
            raise Unknown("finite trace step bound exceeded; no liveness conclusion")
        try:
            self.act(edge.action)
        except DataInvalid:
            if edge.failure is None: raise
            self.state = edge.failure
            return
        # Parallel assignments read the same pre-update snapshot.
        environment=frame_environment(self.frames,self.instance,self.variables,declared_frames=self.contract['frames'])
        updates = {assignment["variable"]: evaluate(assignment["expression"], environment) for assignment in edge.effects}
        # Only a direct alias retains a decoded register's width. Arithmetic
        # assignment never silently inherits an old bit representation.
        width_updates={assignment['variable']:self.register_widths.get(assignment['expression']) for assignment in edge.effects}
        for key,width in width_updates.items():
            if width is None:self.register_widths.pop(key,None)
            else:self.register_widths[key]=width
        for key, value in updates.items():
            normalized=value.value if isinstance(value, Quantity) else value
            kind=self.graph.variable_types[key]
            if kind=='bool' and type(normalized) is not bool or kind in {'int','real'} and type(normalized) not in {int,Fraction}:
                raise ContractError('FSM assignment violates variable type')
            if kind=='int' and Fraction(normalized).denominator!=1:
                raise ContractError('fractional value assigned to integer FSM variable')
            if isinstance(value,Quantity) and value.dimensions:
                raise ContractError('dimensioned FSM variable needs an explicit typed declaration')
        for key,value in updates.items():
            self.variables[key] = value.value if isinstance(value,Quantity) else value
        self.state = edge.target
        if self.state in self.graph.invariants and not self.guard(self.graph.invariants[self.state]):
            raise ContractError("state invariant violated")

    def settle(self):
        while True:
            choices = [e for e in self.graph.edges if e.source == self.state and e.event is None and self.guard(e.guard)]
            if not choices: return
            if len(choices) != 1: raise Unknown("ambiguous enabled epsilon transitions")
            self.take(choices[0])

    def feed(self, event):
        self.settle()
        if Fraction(event.time) < self.now: raise ContractError("trace time went backwards")
        if self.instance is None: self.instance = event.instance
        if event.instance != self.instance and event.operation != "acquire":
            raise ContractError("stale event cannot advance the current operation")
        self.now, self.event = Fraction(event.time), event
        choices = [e for e in self.graph.edges if e.source == self.state
                   and e.event == (event.operation, event.phase) and self.guard(e.guard)]
        if not choices: raise ContractError("event is not enabled by global FSM")
        if len(choices) != 1: raise Unknown("ambiguous enabled FSM transitions")
        if (event.operation,event.phase)==('acquire','logical_success') and not (
                choices[0].action and choices[0].action[0]=='new_acquisition'):
            self.begin_acquisition(event.instance)
        self.events[(event.operation, event.phase, event.instance)] = Event(event.operation,event.phase,event.instance,self.now)
        self.take(choices[0]); self.settle()

    def result(self):
        self.settle()
        return {"state": self.state, "safety": "unknown" if self.graph.gaps or self.state == self.graph.unknown else "satisfied",
                "terminal": self.state in {self.graph.accepting, self.graph.failing},
                "operation_succeeded": self.state == self.graph.accepting,
                "gaps": self.graph.gaps, "liveness": "unknown", "scope": "supplied finite contract trace only"}
