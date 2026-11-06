"""Layered deterministic semantic checks with explicit unknown coverage."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from collections.abc import Mapping
import ast

from .model import Node, ContractError, Unknown, parse, walk, unwrap, identity
from .data import integer, boolean, byte_sequence, check_layout
from .expression import evaluate, quantity, scalar, seconds
from .fsm import lower
from .domains import domains_from_frames,numeric_domain,partition,minimum_wait


@dataclass
class Finding:
    subject: str
    status: str
    check: str
    detail: str


@dataclass
class Report:
    kind: str
    identity: str
    findings: list = field(default_factory=list)

    def add(self,path,status,check,detail):
        self.findings.append(Finding(path,status,check,detail))

    def attempt(self,path,check,operation):
        try:
            operation()
            self.add(path,"satisfied",check,"checked within the stated deterministic scope")
        except Unknown as error:
            self.add(path,"unknown",check,str(error))
        except (ContractError,TypeError,KeyError,ValueError) as error:
            self.add(path,"violated",check,str(error))

    def as_dict(self):
        statuses = {f.status for f in self.findings}
        return {"schema":"autodriver.semantic-report/v1", "kind":self.kind, "contract_sha256":self.identity,
                "status":"violated" if "violated" in statuses else "unknown" if "unknown" in statuses else "satisfied",
                "findings":[asdict(f) for f in self.findings],
                "scope":"listed deterministic checks; no evidence entailment, native candidate execution or universal correctness claim"}


def check_contract(content,kind,*,references=None,evidence_claims=None):
    root = parse(content,kind)
    report = Report(kind,identity(root))
    report.add("", "satisfied", "parse", "bounded constructors, argument binding and immutable data")
    if kind=='fp':
        from .shapes import check_framework_shapes
        check_framework_shapes(root,report)
    names = {}
    frame_domains={}
    relations=None
    if kind=='dbc':
        try:
            frame_domains=domains_from_frames(root)
            from .domains import domains_from_state
            frame_domains=domains_from_state(root,frame_domains)
        except (ContractError,TypeError,KeyError):pass  # Individual layouts report their exact failures below.
        def relation_graph():
            nonlocal relations
            from .relations import Relations
            external=set(frame_domains)
            for _,value in walk(root['behavior']):
                if value.name=='ReadRegister' and isinstance(value['into'],str):external.add(value['into'])
                elif value.name in {'FSM','Lifecycle'}:external.update(value['variables'])
            relations=Relations(root['quantities'],external_names=external)
        report.attempt('quantities','named_relation_dependencies',relation_graph)
    for path,value in walk(root):
        n = value.name
        if n in {"Unspecified","Unresolved"}:
            report.add(path,"unknown","coverage",value["reason"])
        elif n == "ProviderConformanceRequired":
            report.add(path,"unknown","provider",str(value["relation"]))
        elif n == "Constraint":
            def check_constraint():
                if value["origin"] not in {"device_requirement","selected_policy","preserved_acquisition","framework_requirement","implementation_choice"}:
                    raise ContractError("invalid constraint origin")
                if not value["evidence"] or evidence_claims is None:
                    raise Unknown("constraint does not resolve to supplied evidence claims")
                missing=set(value['evidence'])-set(evidence_claims)
                if missing:
                    raise ContractError('Constraint.evidence must name exact claim labels from this response; missing: '+repr(sorted(missing)))
                if value["under"] is not None:
                    raise Unknown("constraint applicability requires an explicit environment")
            report.attempt(path,"origin_and_evidence_reference",check_constraint)
        elif n == "Ref":
            report.attempt(path,"parent_reference",lambda: references.resolve(value) if references else (_ for _ in ()).throw(Unknown("parent registry not supplied")))
        elif n == "I2C":
            report.attempt(path,"address",lambda: integer(value["address_7bit"],0,127))
        elif n == "SPI":
            report.attempt(path,"spi_mode",lambda: integer(value["mode"],0,3))
        elif n in {"FrameLayout","CRCProtectedWords"}:
            report.attempt(path,"layout_and_integrity_ranges",lambda:check_layout(value))
        elif n == "CRC8":
            report.attempt(path,"crc_parameters",lambda: [integer(value[k],0,255) for k in ("polynomial","initial","final_xor")]
                           +[boolean(value[k]) for k in ("reflect_input","reflect_output")])
        elif n in {"WriteBytes","WriteSegment"}:
            report.attempt(path,"bytes",lambda:byte_sequence(value["data"]))
        elif n in {"ReadSegment","FrameLayout"}:
            report.attempt(path,"length",lambda:integer(value["length"],0,65536))
        elif n in {"WaitAfterCompletion","WaitFrom","Deadline"}:
            def check_wait():
                lower_bound = value.get("minimum")
                upper_bound = value.get("maximum")
                if lower_bound is not None:
                    bound=unwrap(lower_bound)
                    if isinstance(bound,Node) and bound.name=='ConditionalValue':
                        result=minimum_wait(bound,domains=root['applicable_conditions'] if kind=='dbc' else {})
                        if result['status']=='violated':raise ContractError('conditional wait branches overlap or miss declared inputs')
                        if result['status']!='satisfied':raise Unknown('conditional physical bound could not be established')
                    else:seconds(bound)
                if upper_bound is not None: seconds(unwrap(upper_bound))
                if lower_bound is not None and upper_bound is not None and seconds(unwrap(lower_bound)) > seconds(unwrap(upper_bound)):
                    raise ContractError("minimum wait exceeds maximum")
                anchor = value.get("anchor",value.get("of"))
                if isinstance(anchor, Node) and anchor.name == "EventRef":
                    if anchor["phase"] in {"submit","accepted","rejected","callback_success","callback_failure"} and kind == "dbc":
                        raise ContractError("DBC wait cannot anchor on a native framework event")
                    if anchor["instance"] != "current": raise ContractError("wait references an old operation")
                if n == "WaitAfterCompletion" and isinstance(anchor, Node) and anchor["phase"] != "physical_success":
                    raise ContractError("completion wait requires physical success")
            report.attempt(path,"physical_time_units_and_anchor",check_wait)
        elif n == "EventRef":
            report.attempt(path,"event_phase",lambda: _require(value["phase"] in {
                "physical_start","physical_success","physical_failure","submit","accepted","rejected",
                "callback_success","callback_failure","logical_success","logical_failure","commit"},"unknown event phase"))
        elif n == "PhysicalQuantity":
            report.attempt(path,"physical_unit",lambda: quantity(1,value["unit"]))
            def expression_syntax():
                expression=value['expression']
                if not isinstance(expression,str) or not expression or len(expression)>65536:raise ContractError('bounded physical expression string required')
                try:ast.parse(expression,mode='eval')
                except (SyntaxError,RecursionError) as error:raise ContractError('physical expression syntax invalid: '+str(error)) from None
                from .expression import check_pure_expression
                check_pure_expression(expression)
                if value.get('bit_width') is not None:integer(value['bit_width'],1,4096)
            report.attempt(path,'physical_expression_syntax',expression_syntax)
            def check_domain():
                domain=value['domain']
                domains=domain if isinstance(domain,Mapping) else frame_domains
                current=value
                definitions=None
                preconditions=None;widths=None
                if relations is not None and path.startswith('quantities.'):
                    name=path[len('quantities.'):]
                    definitions=relations.solver_definitions(name)
                    preconditions=relations.solver_preconditions(name)
                    widths=relations.solver_widths(name)
                checked=numeric_domain(current,domains,definitions=definitions,preconditions=preconditions,definition_widths=widths)
                if checked['status']=='violated':raise ContractError('physical expression may be undefined: '+str(checked.get('counterexample')))
                if checked['status']!='satisfied':raise Unknown(checked.get('reason','numeric domain not established'))
            report.attempt(path,'numeric_definedness_over_declared_bounds',check_domain)
        elif n == "OutputEncoding":
            def check_encoding():
                quantity(1,value["unit"])
                if not value["scale"]: raise ContractError("output scale must be nonzero")
            report.attempt(path,"encoding_scale_and_unit",check_encoding)
        elif n == 'ElapsedTimeLowerBound':
            from .delay import lower_bound
            report.attempt(path,'synchronous_delay_declaration',lambda:lower_bound(value))
        elif n == "Rounding":
            report.attempt(path,"rounding_mode",lambda: _require(value["mode"] in {"nearest_ties_even","toward_zero","floor","ceil"}
                           and value["overflow"] in {"report_gap","saturate"},"invalid rounding/overflow mode"))
        elif n == "ConditionalValue":
            def check_partition():
                default=value['otherwise']
                otherwise=not isinstance(default,Node) or default.name not in {'Unspecified','Unresolved'}
                checked=partition(value['cases'],root['applicable_conditions'] if kind=='dbc' else {},otherwise=otherwise)
                if checked['status']=='violated':raise ContractError('conditional branches overlap or fail to cover declared domain')
                if checked['status']!='satisfied':raise Unknown('conditional partition requires an encoded input domain/predicate')
            report.attempt(path,'conditional_partition',check_partition)
        elif n == "Progress":
            report.add(path,"unknown","liveness","finite safety execution cannot prove eventual progress")
        if n in {"WriteBytes","Transfer","ReadRegister","WriteRegister","UpdateRegister","ReadFrame","DriveSignal","SampleSignal"}:
            name = value.get("name")
            if name:
                if name in names: report.add(path,"violated","operation_identity","duplicate operation name: "+name)
                names[name] = path
        if kind=='dbc' and n in {'Transfer','ReadRegister','WriteRegister'}:
            def check_wire():
                from .wire import transaction
                connection=unwrap(root['connection'])
                if not isinstance(connection,Node) or connection.name not in {'I2C','SPI'}:raise Unknown('typed I2C/SPI connection required for wire check')
                transaction(root,value,connection.name.lower())
            report.attempt(path,'typed_wire_transaction',check_wire)
    if kind == "dbc":
        if not isinstance(root['behavior'],Node) or root['behavior'].name not in {'Lifecycle','FSM','Unspecified','Unresolved'}:
            report.add('behavior','violated','global_behavior_root','one global Lifecycle or FSM is required')
        def check_graph():
            graph = lower(root["behavior"])
            reachable = graph.reachable()
            declared_events={edge.event for edge in graph.edges if edge.event is not None}
            for path,value in walk(root['behavior'],'behavior'):
                if value.name not in {'WaitFrom','WaitAfterCompletion','Deadline'}:continue
                anchor=value['of'] if value.name=='WaitAfterCompletion' else value['anchor']
                if not isinstance(anchor,Node) or anchor.name!='EventRef':continue
                event=(anchor['operation'],anchor['phase'])
                if event not in declared_events:
                    choices=sorted(operation for operation,phase in declared_events if phase==anchor['phase'])
                    report.add(path,'violated','declared_anchor_event',
                        'anchor does not match an event in the derived global graph: '+str(event)+'; declared operations for this phase: '+str(choices))
                else:report.add(path,'satisfied','declared_anchor_event','exact operation/phase resolves in the same global graph; current occurrence and time checked during execution')
            if graph.accepting not in reachable: raise Unknown("no success terminal reachable in derived global FSM")
            for gap in graph.gaps: report.add(gap["path"],"unknown","fsm_coverage",gap["reason"])
            report.add("behavior","satisfied","global_fsm_reachability",f"{len(reachable)} structurally reachable control states; guards may narrow this")
        report.attempt("behavior","lower_global_fsm",check_graph)
        for path,value in walk(root["behavior"],"behavior"):
            if value.name == "ReadFrame" and value["frame"] not in root["frames"]:
                report.add(path,"violated","frame_reference","read refers to an undeclared frame")
            if value.name == "WaitAfterCompletion" and isinstance(value["of"],str) and value["of"] not in names:
                report.add(path,"violated","operation_reference","wait refers to an undeclared operation")
        from .dataflow import check_materialization
        check_materialization(root,report)
    # Critical assertion support is a different check from a line reference.
    report.add("evidence","unknown","evidence_entailment","original assertion support must be checked during bounded evidence refinement")
    return root,report.as_dict()


def _require(condition,message):
    if not condition: raise ContractError(message)


def include_response_gaps(report,gaps):
    """Preserve required/uncertain gaps declared outside the constructor.

    A model's optional label does not exempt any deterministic finding. This
    only prevents envelope gaps from disappearing on parsing or saved import.
    """
    if not isinstance(gaps,(list,tuple)) or len(gaps)>1024:raise ContractError('bounded response gaps required')
    if not gaps:return report
    findings=list(report['findings'])
    for index,gap in enumerate(gaps):
        if not isinstance(gap,dict) or set(gap)!={'subject','reason','impact'} or gap['impact'] not in {'required','optional','unknown'}:
            raise ContractError('invalid declared response gap')
        if gap['impact']!='optional':
            findings.append({'subject':'response.unresolved.'+str(index)+'.'+gap['subject'],'status':'unknown',
                'check':'declared_response_gap','detail':gap['reason']})
    statuses={finding['status'] for finding in findings}
    return dict(report,findings=findings,status='violated' if 'violated' in statuses else 'unknown' if 'unknown' in statuses else 'satisfied',
                declared_response_gaps=list(gaps))
