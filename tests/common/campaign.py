"""Durable task-granularity resume, host progress and preserved attempt history."""
from contextlib import contextmanager
import csv
import fcntl
import json
import threading
import time
from pathlib import Path

from benchmark.common.api import load, save, digest, inventory, relative
from tests.common.recovery import archived_recovery
from benchmark.common.evaluation.report import summary_columns


@contextmanager
def campaign_lock(control):
    control.mkdir(parents=True, exist_ok=True)
    with (control / 'campaign.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('This experiment batch is already running') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def output_directory(root, name):
    path = Path(name)
    if not path.is_absolute():
        path = root / relative(str(path))
    if not path.resolve().is_relative_to((root / 'result').resolve()):
        raise ValueError('Output root must be inside artifact result/')
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Linked output paths are not supported')
    return path


def recovery(report, trial):
    value = archived_recovery(report)
    if value is not None:
        return value
    # Legacy correctness failures remain final, including ordinary time limits.
    if report['status'] == 'infrastructure_error':
        execution = trial / 'execution/report.json'
        completed = execution.exists() and load(execution).get('result', {}).get('status') == 'candidate_ready'
        return {'retryable': True, 'mode': 'evaluate' if completed and (trial / 'submission').exists() else 'regenerate'}
    return {'retryable': False}


def summaries(batch_root):
    """Keep selected-candidate scores and separately aggregate ALL attempt costs."""
    from tests.common.pipeline import archive
    from tests.common.execution.accounting import summarize
    configuration = load(batch_root / 'config.json') if (batch_root / 'config.json').is_file() else {}
    if not any((batch_root / 'trials').glob('*/*/*/evaluation/report.json')) and not configuration.get('planned_trials'):
        return
    archive(batch_root)
    path = batch_root / 'summary.csv'
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        key = Path(row['task_id']) / row['condition'] / row['repeat']
        historical = sorted((batch_root / 'attempts' / key).glob('attempt-*'))
        current = batch_root / 'trials' / key
        sources = historical + ([current] if current.is_dir() else [])
        records = {}
        missing_sources = []
        for trial in sources:
            file = trial / 'execution/requests.jsonl'
            if file.exists():
                for line in file.read_text().splitlines():
                    record = json.loads(line)
                    ident = record['id']
                    if ident in records and records[ident] != record:
                        raise ValueError('Conflicting request accounting across attempts')
                    records[ident] = record
            else:
                record = load(trial / 'trial.json') if (trial / 'trial.json').is_file() else {}
                archive_state = load(trial / 'execution/archive-state.json') if (trial / 'execution/archive-state.json').is_file() else {}
                if not record.get('execution_origin') and record.get('method') != 'reference' and archive_state.get('execution_started') is not False:
                    missing_sources.append(str(trial.relative_to(batch_root)))
        usage = summarize(list(records.values()))
        row.update(attempt_count=len(sources), all_attempts_requests=usage['requests'],
                   all_attempts_input_tokens_known=usage['input_tokens']['known_total'],
                   all_attempts_output_tokens_known=usage['output_tokens']['known_total'],
                   all_attempts_cost_lower_cny=usage['cost_lower_cny'],
                   all_attempts_cost_upper_cny=usage['cost_upper_cny'],
                   all_attempts_accounting_complete=usage['accounting_complete'])
        from tests.common.execution.costs import trial_cost_columns
        total=trial_cost_columns(configuration,{'method':configuration.get('method'),
            'task_id':row['task_id'],'condition':row['condition'],'repeat':int(row['repeat'])},usage)
        for bound in ('lower','upper'):
            row['all_attempts_total_known_cost_'+bound+'_cny']=total['total_known_cost_'+bound+'_cny']
        row['attempts_missing_request_archive'] = len(missing_sources)
        if missing_sources:
            row.update(all_attempts_requests='', all_attempts_cost_upper_cny='', all_attempts_accounting_complete=False,
                       all_attempts_total_known_cost_upper_cny='')
    from tests.common.archiving import write_csv
    write_csv(path, rows)
    summary = load(batch_root / 'summary.json')
    summary['csv_sha256'] = digest(path)
    summary['trials_with_missing_request_archives'] = sum(int(r['attempts_missing_request_archive']) > 0 for r in rows)
    save(batch_root / 'summary.json', summary)


def execute(root, spec, batch, rows, *, output_root='result',
            retry_infrastructure=False, allow_budget_change=False, heartbeat_seconds=60,
            verification_group=None):
    from tests.common.execution.config import load_host, public_host_config
    from tests.common.execution.budget import Ledger
    from tests.common.trials import run_trial
    from benchmark.common.api import resolve_task, owned
    from method.autodriver.implementation.system.variants import variant
    variant_name=spec.get('variant','full');variant(variant_name)
    if variant_name!='full' and spec['methods']!=['autodriver']:
        raise ValueError('Mechanism interventions require an autodriver-only campaign')
    for row in rows:
        if row['method']=='autodriver':
            if row.get('variant','full')!=variant_name or row['condition']!='SD':
                raise ValueError('Campaign row differs from its trusted SD intervention')
        elif row.get('variant','full')!='full':raise ValueError('Foreign method cannot receive an autodriver intervention')
    if len(relative(batch).parts) != 1 or len(relative(spec['name']).parts) != 1:
        raise ValueError('Batch and experiment must be single path components')
    output_root = output_directory(root, output_root)
    control = output_root / 'acceptance' / batch
    with campaign_lock(control):
        def emit(message):
            print(time.strftime('[%Y-%m-%d %H:%M:%S] ') + message, flush=True)
        emit('VERIFY release and source receipts (no source compilation)')
        from tests.common.release_session import ReleaseSession
        session_name = verification_group or batch
        if len(relative(session_name).parts) != 1:
            raise ValueError('Verification group must be one path component')
        if len(spec['methods']) != 1:
            raise ValueError('A live batch requires one independently bound method')
        session = ReleaseSession(root, output_root / 'acceptance/release-sessions' / session_name, method=spec['methods'][0])
        release = session.verify()
        emit(f"VERIFIED mode={session.last['mode']} seconds={session.last['seconds']} entries={session.last['entries']}")
        configuration = public_host_config(load_host(root))
        if spec['model'] != configuration['model']:
            raise ValueError('Experiment model differs from host configuration')
        snapshot = load(root / 'manifest.json')
        if snapshot['release_identity'] != release['release_identity']:
            raise ValueError('Release changed during experiment admission')
        release_record = control / 'experiment-release.json'
        if release_record.exists() and load(release_record) != snapshot:
            raise ValueError('Cannot resume against a different frozen release')
        ledger_path = control / 'budget.sqlite'
        destinations = {}
        configurations = {}
        for method in spec['methods']:
            destination = output_root / method / spec['name'] / batch
            destinations[method] = destination
            expected = {'schema': 'autodriver.experiment-run/v1', 'definition': spec,
                        'method': method, 'host': configuration,
                        'planned_trials': [{k:r[k] for k in ('task_id', 'condition', 'repeat')}
                                           | ({'variant':r.get('variant','full')} if method=='autodriver' else {})
                                           for r in rows if r['method'] == method],
                        'online_settings': {'reasoning': {'effort': 'medium'}, 'max_output_tokens': 16384,
                                            'request_timeout_seconds': 300},
                        'release_identity': release['release_identity'],
                        'dependency_identities': release.get('dependency_identities'),
                        'pricing_sha256': digest(root / 'dependencies/autodl-pricing.json'),
                        'knowledge': {}}
            if destination.exists():
                actual = load(destination / 'config.json')
                compared = json.loads(json.dumps(actual))
                if allow_budget_change:
                    compared['definition']['budget']['total_cny'] = spec['budget'].get('total_cny')
                if compared != expected:
                    raise ValueError('Cannot resume a batch with changed configuration')
            configurations[method] = expected
        # Admission validates everything before changing an existing ledger cap.
        cap = spec['budget'].get('total_cny')
        if ledger_path.exists() and allow_budget_change:
            ledger = Ledger.for_run(ledger_path); ledger.set_limit(cap)
        else:
            ledger = Ledger(ledger_path, cap)
        save(release_record, snapshot)
        for method, expected in configurations.items():
            destination = destinations[method]
            old = destination / 'config.json'
            if old.exists() and load(old) != expected:
                save(control / f'configuration-before-{time.time_ns()}.json', load(old))
            save(old, expected)
            summaries(destination)
        completed = []
        current = {}
        stop = threading.Event()
        state_lock = threading.Lock()
        from tests.common.execution.progress import ProgressBudget
        progress_budget = ProgressBudget(ledger)

        def publish(status):
            bill, accounting = progress_budget.read()
            save(control / 'progress.json', {'status': status, 'total': len(rows), 'finished': len(completed),
                                             'active': dict(current), 'trials': list(completed), 'updated': time.time(),
                                             'accounting': accounting})
            if accounting['status'] == 'fresh':
                save(control / 'budget.json', bill)
            else:
                emit('WARNING accounting=' + accounting['status'] + ': ' + accounting['message'])
            return bill, accounting

        def heartbeat():
            while not stop.wait(heartbeat_seconds):
                with state_lock:
                    if current:
                        bill, accounting = publish('running')
                        costs = (f"batch_requests={len(bill['requests'])} accounted_upper={bill['accounted_upper_cny']:.4f} CNY"
                                 if bill is not None else 'batch_requests=unavailable accounted_upper=unavailable')
                        emit(f"[{len(completed)+1}/{len(rows)}] RUN {current['task_id']} {current['condition']} "
                             f"phase={current.get('phase')} elapsed={int(time.monotonic()-current['started'])}s "
                             f"{costs} accounting={accounting['status']}")
        thread = threading.Thread(target=heartbeat, daemon=True); thread.start()
        status = 'completed'
        try:
            for row in rows:
                task = resolve_task(row['task_id'])
                destination = destinations[row['method']]
                key = Path(row['task_id']) / row['condition'] / str(row['repeat'])
                trial = destination / 'trials' / key
                episode = f"{batch}-{row['method']}-{row['task_id']}-{row['condition']}-{row['repeat']}"
                if row['method']=='autodriver':episode+='-'+spec['name']+'-'+variant_name
                workspace = root / 'run' / episode
                archive_state = trial / 'execution/archive-state.json'
                if archive_state.exists() and load(archive_state).get('status') != 'complete':
                    raise ValueError('Unfinished archive requires explicit archive recovery: ' + str(trial))
                if workspace.exists():
                    raise ValueError('Existing trial workspace requires process-state review: ' + str(workspace))
                receipt = control / 'completions' / row['method'] / key / 'receipt.json'
                previous = None
                attempts = destination / 'attempts' / key
                attempt = len(list(attempts.glob('attempt-*')))
                pending_path = control / 'recoveries' / row['method'] / key / 'pending.json'
                if not trial.exists() and pending_path.exists():
                    pending = load(pending_path)
                    if pending['mode'] not in ('evaluate', 'regenerate'):
                        raise ValueError('Explicit response recovery required; ordinary resume cannot regenerate')
                    saved = root / relative(pending['previous'])
                    if not saved.is_relative_to(attempts) or inventory(saved) != pending['files']:
                        raise ValueError('Recovery archive changed')
                    previous = root / pending['previous'] if pending['mode'] == 'evaluate' else None
                if trial.exists():
                    if receipt.exists() and inventory(trial) != load(receipt)['files']:
                        raise ValueError('Archived trial changed; refusing reuse or regeneration: ' + str(key))
                    if not (trial / 'trial.json').exists() or not (trial / 'evaluation/report.json').exists():
                        raise ValueError('Incomplete archive requires process-state review: ' + str(trial))
                    result = load(trial / 'evaluation/report.json')
                    retry = recovery(result, trial)
                    if result.get('recovery') != retry:
                        save(control / 'recovery-decisions' / row['method'] / key / 'decision.json', {
                            'report_sha256': digest(trial / 'evaluation/report.json'),
                            'recorded_recovery': result.get('recovery'), 'effective_recovery': retry})
                        emit(f"[{len(completed)+1}/{len(rows)}] RECOVERY_DECISION {key} "
                             f"retryable={retry.get('retryable')} reason={retry.get('reason', 'legacy')}")
                    if retry_infrastructure and retry.get('retryable'):
                        if retry.get('mode') not in ('evaluate', 'regenerate'):
                            raise ValueError('Explicit response recovery required: ' + str(retry.get('mode')) +
                                             '; no new attempt or upstream request was started')
                        target = attempts / f'attempt-{attempt:03d}'
                        target.parent.mkdir(parents=True, exist_ok=True)
                        save(pending_path, {'previous': str(target.relative_to(root)), 'mode': retry['mode'], 'files': inventory(trial)})
                        # Rename keeps every old patch, response, cost and report.
                        trial.rename(target)
                        receipt.unlink(missing_ok=True)
                        previous = target if retry.get('mode') == 'evaluate' else None
                        attempt += 1
                        emit(f"[{len(completed)+1}/{len(rows)}] RETRY {key} mode={retry['mode']} attempt={attempt}")
                    else:
                        emit(f"[{len(completed)+1}/{len(rows)}] SKIP {key} status={result['status']}")
                        if not receipt.exists():
                            save(receipt, {'files': inventory(trial)})
                        completed.append({**row, 'status': result['status']})
                        continue
                with state_lock:
                    current = {**row, 'phase': 'start', 'started': time.monotonic(), 'attempt': attempt}
                    emit(f"[{len(completed)+1}/{len(rows)}] START {key} attempt={attempt}")
                    publish('running')
                def progress(phase):
                    with state_lock:
                        current['phase'] = phase
                        emit(f"[{len(completed)+1}/{len(rows)}] PHASE {key} {phase}")
                        publish('running')
                result = run_trial(method=row['method'], task_id=row['task_id'], condition=row['condition'],
                                   repetition=row['repeat'], experiment=spec['name'], batch=batch,
                                   model=spec['model'], budget=spec['budget'].get('trial', {}),
                                   ledger_path=ledger_path,
                                   batch_root=destination, previous=previous, attempt=attempt, progress=progress,
                                   control_root=control, release_session=session,variant=row.get('variant','full'))
                save(receipt, {'files': inventory(trial)})
                pending_path.unlink(missing_ok=True)
                summaries(destination)
                with state_lock:
                    completed.append({**row, 'status': result['status']})
                    detail = summary_columns(result)
                    emit(f"[{len(completed)}/{len(rows)}] END {key} status={result['status']} " +
                         " ".join(f"{name}={value}" for name, value in detail.items() if value))
                    current = {}; publish('running')
                if recovery(result, trial).get('retryable'):
                    status = 'blocked'
                    emit('BLOCKED by infrastructure; fix the cause, then resume this batch. No later tasks started.')
                    break
            return {'status': status, 'trials': completed, 'budget': ledger.summary()}
        except BaseException:
            status = 'interrupted'
            raise
        finally:
            stop.set(); thread.join()
            with state_lock:
                current = {}; publish(status)
            for destination in destinations.values():
                summaries(destination)
            save(control / 'experiment.json', {'status': status, 'trials': completed, 'budget': ledger.summary()})
            emit(f"{status.upper()} finished={len(completed)}/{len(rows)}")
