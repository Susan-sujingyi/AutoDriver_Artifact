"""Host method execution. Provider credentials never enter the child environment."""
import json
import os
from pathlib import Path
import secrets
import sys
import time

from benchmark.common.api import ROOT, digest, inventory, load, save
from tests.common.sandbox import tool_roots
from .accounting import summarize
from .budget import Ledger,BudgetDenied
from .config import load_host
from .gateway import Gateway
from .process import execute


def public_environment(agent, model, port, token, settings, *, deadline_seconds=1800, reuse=False,
                       method='autodriver'):
    state = agent / 'output'
    home = state / 'home'
    if not reuse:
        state.mkdir(exist_ok=True)
    tmp = state / 'tmp'
    for path in (home, tmp):
        if reuse:
            if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(agent):
                raise ValueError("Native continuation requires unchanged local environment directories")
        else:
            path.mkdir(exist_ok=False)
    rust = Path(os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")))
    cargo_bin = Path.home() / ".cargo/bin"
    environment = {
        "PATH": os.pathsep.join([str(ROOT / ".venv/bin"), str(cargo_bin),
                                str(ROOT / "third_party/tools/kconfig-frontends/usr/bin"), "/usr/bin", "/bin"]),
        "HOME": str(home), "TMPDIR": str(tmp),
        "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8",
        "PYTHON": str(ROOT / ".venv/bin/python"), "PYTHONPATH": str(ROOT),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "RUSTUP_HOME": str(rust), "RUSTUP_TOOLCHAIN": "nightly-2024-01-01",
        "AUTODRIVER_RUST_VENDOR": str(ROOT / "third_party/rust_vendor/sources"),
        "OPENAI_MODEL": model,
        "AUTODRIVER_BOUNDED_GATEWAY_URL": f"http://127.0.0.1:{port}/v1",
        "AUTODRIVER_GATEWAY_TOKEN": token,
        "AUTODRIVER_MODEL_SETTINGS": json.dumps(settings),
        "AUTODRIVER_METHOD_LOGS": str(agent / "output/requests"),
        "AUTODRIVER_GATEWAY_WAIT_SECONDS": str(deadline_seconds + 5)}
    public_tools = [p for p in (cargo_bin, rust / "toolchains", rust / "settings.toml") if p.exists()]
    return environment, public_tools


def method_files(name):
    """No grant on method/ or the generated knowledge ancestor directory."""
    if name != 'autodriver':
        raise ValueError('Only AutoDriver is included in this artifact')
    base = ROOT / 'method/autodriver'
    return [ROOT / 'method/common', base / 'adapter.py', base / 'implementation/system']


def request_records(gateway):
    # The transactional ledger is authoritative even when a controller dies
    # after reserving funds but before journaling or exporting that request.
    # Missing event/response evidence stays unknown; it never drops the charge.
    responses = {e["id"]: e for e in gateway.events if e["event"] == "response"}
    errors = {e["id"]: e for e in gateway.events if e["event"] == "error" and e.get("id")}
    reserved = {e["id"]: e for e in gateway.events if e["event"] == "reserved"}
    repairs = {e["id"]: e for e in gateway.events if e["event"] == "local_settlement_recovered"}
    skipped = {e["id"]: e for e in gateway.events if e["event"] == "local_settlement_recovery_skipped"}
    rows = []
    from .accounting import estimate, undispatched
    from .budget import micro_cny
    for charged in gateway.ledger.summary()["requests"]:
        if charged["episode"] != gateway.episode:
            continue
        if charged["id"] in getattr(gateway, "prior_request_ids", set()):
            # A later invocation may share the budget episode. Its archive
            # must not duplicate or relabel requests owned by an old attempt.
            # Explicit replay/settlement has separate host audit records.
            continue
        rid = charged["id"]
        event = reserved.get(rid, {})
        response = responses.get(rid, {})
        step = event.get('method_step')
        step = step if isinstance(step,str) else None
        repair = step.rsplit(':',1)[-1] if step and event.get('method_stage')=='repair' else None
        repair = repair if repair in {'fp','dbc','bp','code'} else None
        finished = response.get('time') or errors.get(rid,{}).get('time')
        rows.append({"schema": "autodriver.api-request/v1", "id": rid, "episode": gateway.episode,
                     "stage": event.get("method_stage") or "generation",
                     "method_step": step, "call_id": step.rsplit(':',1)[0] if repair else step,
                     "repair_target": repair,
                     "finished_time": finished,
                     "elapsed_seconds": max(0,finished-event['time']) if finished is not None and event.get('time') is not None else None,
                     "requested_model": gateway.model, "response_model": response.get("response_model"),
                     "reserved_micro_cny": micro_cny(charged["reserved_upper_cny"]),
                     "accounting": (undispatched(gateway.pricing) if charged['status']=='not_dispatched'
                                    else response.get("accounting") or estimate(charged.get("usage"), gateway.pricing)),
                     "accounting_evidence": "gateway_response" if response.get("accounting") else "durable_ledger",
                     "ledger_status": charged["status"],
                     "response_status": response.get("response_status"),
                     "response_id": response.get("response_id"),
                     "request_sha256": event.get("request_sha256"),
                     "logical_request_id": event.get("logical_request_id"),
                     "attempt": event.get("attempt"),
                     "retry_policy": event.get("retry_policy"),
                     "local_settlement_policy": ("saved-receipt-local-settlement/v1"
                         if getattr(gateway, "repair_settlement", False) else "disabled"),
                     "local_recovery": repairs.get(rid),
                     "local_recovery_blocked": skipped.get(rid),
                     "same_request_previous_id": event.get("same_request_previous_id"),
                     "retry_relationship": "same_request_observed" if event.get("same_request_previous_id")
                                           else "not_supplied",
                     "error": errors.get(rid),
                     "request_time": event.get("time")})
    return rows


def method_command(name):
    system_package = (
        'p=types.ModuleType("method.autodriver.implementation.system");'
        f'p.__path__=[{str(ROOT / "method/autodriver/implementation/system")!r}];'
        'sys.modules["method.autodriver.implementation.system"]=p;') if name == 'autodriver' else ''
    program = (
        'import json,importlib,importlib.util,types,sys;from pathlib import Path;'
        'p=types.ModuleType("method");'
        f'p.__path__=[{str(ROOT / "method")!r}];sys.modules["method"]=p;'
        'p=types.ModuleType("method.common");'
        f'p.__path__=[{str(ROOT / "method/common")!r}];sys.modules["method.common"]=p;'
        f'p=types.ModuleType("method.{name}");'
        f'p.__path__=[{str(ROOT / "method" / name)!r}];sys.modules["method.{name}"]=p;'
        f'p=types.ModuleType("method.{name}.implementation");'
        f'p.__path__=[{str(ROOT / "method" / name / "implementation")!r}];'
        f'sys.modules["method.{name}.implementation"]=p;'
        + system_package +
        f's=importlib.util.spec_from_file_location("method.{name}.adapter",'
        f'{str(ROOT / "method" / name / "adapter.py")!r});'
        'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
        'r=m.run(json.loads(Path("output/request.json").read_text()));'
        'Path("output/method-result.json").write_text(json.dumps(r))')
    return [str(ROOT / ".venv/bin/python"), "-c", program]


def run_live(name, agent, request, output, *, ledger_path, knowledge=None):
    if name != 'autodriver':
        raise ValueError("Live adapter not implemented: " + name)
    if name=='autodriver' and request.get('condition')!='SD':raise ValueError('new autodriver live execution admits SD only')
    if knowledge is not None:
        raise ValueError('This standalone runner recovers fresh framework profiles for each trial')
    agent, output = Path(agent).resolve(strict=True), Path(output)
    if not agent.is_relative_to(ROOT / "run"):
        raise ValueError("Agent workspace must be inside run/")
    if request.get("operation") != "run":
        raise ValueError("Expected a live method request")
    configuration = load_host(ROOT)
    if request["model"] != configuration["OPENAI_MODEL"]:
        raise ValueError("Requested and host-configured models differ")
    budget = request["budget"]
    from .code_budget import configured_limits
    configured_limits(budget)
    seconds = min(int(budget.get("seconds", 1800)), 1800)
    cap = min(float(budget.get("cny", 20)), 20)
    max_requests = min(int(budget.get("requests", 64)), 64)
    if seconds <= 0 or cap <= 0 or max_requests <= 0:
        raise ValueError("Positive explicit trial budget required")
    output.mkdir(parents=True, exist_ok=False)
    settings = {"reasoning": {"effort": "medium"}, "max_output_tokens": 16384}
    request = dict(request, workspace=str(agent), budget=dict(budget,
        seconds=seconds, cny=cap, requests=max_requests))
    save(agent / "output/request.json", request)
    tools=None
    if name=='autodriver':
        from tests.common.autodriver_tools import CandidateTools
        from tests.common.autodriver_gateway import AutodriverGateway
        from method.autodriver.implementation.system.variants import variant
        from method.autodriver.implementation.system.candidate_policy import CANDIDATE_ADMISSION_POLICY
        variant_name=request.get('variant','full');variant(variant_name)
        tools=CandidateTools(agent,agent.parent/'host/baseline',agent.parent/'host/autodriver-tools',
            variant_name=variant_name,deadline=time.monotonic()+seconds,
            admission_policy=CANDIDATE_ADMISSION_POLICY)
        gateway_factory=AutodriverGateway;gateway_extra={'candidate_tools':tools}
    gateway = gateway_factory(provider_url=configuration["OPENAI_BASE_URL"], model=request["model"],
                      api_key=configuration["OPENAI_API_KEY"],
                      pricing_path=ROOT / "dependencies/autodl-pricing.json",
                      ledger_path=Path(ledger_path), episode=request["episode"],
                      ledger=Ledger.for_run(ledger_path),
                      token=secrets.token_urlsafe(32), episode_limit_cny=cap,
                      max_requests=max_requests, max_output_tokens=16384,
                      deadline_seconds=seconds, request_timeout_seconds=min(300, seconds),
                      reasoning_effort="medium", repair_settlement=True,**gateway_extra)
    if tools is not None:tools.deadline=min(tools.deadline,gateway.deadline)
    result = {"status": "infrastructure_error"}
    try:
        env, rust_tools = public_environment(agent, request["model"], gateway.port, gateway.token, settings,
                                             deadline_seconds=seconds, method=name)
        if name=='autodriver':
            env['AUTODRIVER_METHOD_VARIANT']=variant_name
        readable = tool_roots(ROOT) + rust_tools + method_files(name) + [
            agent, ROOT / "third_party/rust_vendor"]
        remaining = gateway.deadline - time.monotonic()
        if remaining <= 0:
            result.update(status='not_completed', timed_out=True,
                          reason='Episode deadline reached before method start', cleanup={'status':'pass'})
        else:
            result = execute(method_command(name), cwd=agent,
                             readonly=readable, writable=[agent / "output", Path("/dev/null")],
                             environment=env, output=output / "process",
                             gateway_port=gateway.port, timeout=remaining)
        if (agent / "output/method-result.json").exists():
            from .artifacts import read_regular
            result["result"] = json.loads(read_regular(agent / "output/method-result.json"))
        # Public inputs must still match the pre-method manifest. Candidate
        # scope is checked separately by the canonical patch collector.
        expected = load(agent / "materials.json")["files"]
        changed = [p for p, sha in expected.items()
                   if not p.startswith(("target/", "output/")) and (
                       not (agent / p).is_file() or (agent / p).is_symlink()
                       or digest(agent / p) != sha["sha256"]
                       or bool((agent / p).stat().st_mode & 0o111) != sha["executable"])]
        if changed:
            result.update(status="submission_rejected", reason="Public materials changed", paths=changed)
    except BaseException as error:
        result.update(status="not_completed" if isinstance(error, (KeyboardInterrupt, SystemExit))
                      else "infrastructure_error", reason=type(error).__name__)
        raise
    finally:
        gateway.close()
        process_record = output / "process/process.json"
        if process_record.is_file():
            result["cleanup"] = load(process_record).get("cleanup", {"status": "missing"})
        if tools is not None and result.get('cleanup',{}).get('status')=='pass':
            try:
                method_result=result.get('result',{});pipeline_result=method_result.get('pipeline',{})
                eligible=(method_result.get('candidate')=='host_managed_final_candidate'
                    and pipeline_result.get('final_candidate_disposition')=='last_applied_candidate')
                frozen=tools.freeze_final(output/'candidate-freeze.json',expected_revision=pipeline_result['code_proposal']['revision']) if eligible else None
                if frozen is not None:
                    import shutil
                    shutil.copytree(tools.last_candidate/'output/submission',output/'final-submission')
                    result['final_submission']='final-submission';result['candidate_freeze']=frozen
                elif tools.last_candidate is not None:
                    result['candidate_selection']={'status':'no_submission','reason':'latest method result did not deliver an eligible final candidate; older applied sources retained only as history'}
            except (ValueError,OSError,KeyError,TypeError) as error:
                # Fee archives remain mandatory even when source collection
                # or tool cleanup refuses a final candidate.
                result.update(status='submission_rejected',reason='candidate freeze refused: '+type(error).__name__)
            try:tools.archive(output/'candidate-tools')
            except (ValueError,OSError) as error:
                result.update(status='infrastructure_error',reason='candidate tool archive failed: '+type(error).__name__)
        rows = request_records(gateway)
        for row in rows:
            row.update(method=name, task_id=request["task_id"], condition=request["condition"],
                       repetition=request["repetition"])
        (output / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        save(output / "usage.json", summarize(rows))
        save(output / "gateway-events.json", list(gateway.events))
        from .termination import classify
        result['termination'] = classify(result, gateway.events)
        result["model_calls"] = len(rows)
        save(output / "report.json", result)
    return result
