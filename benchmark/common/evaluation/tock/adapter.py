"""Common offline executor for versioned device models, independent of methods.

Device profiles provide a catalog and an independent oracle/rendering module.
Neither a legacy public contract nor a candidate's IR is used as truth.
"""
from __future__ import annotations

from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import time

from benchmark.common.target.tock.dependencies import create_cargo_environment
from benchmark.common.api import digest, file_map, identity as map_digest, ROOT, load, owned
TOOLCHAIN = 'nightly-2024-01-01'
from benchmark.common.execution import (compile_lock as _compile_lock, run as _run, save,
                           interruption_handler)

STATES = ('pass', 'fail', 'not_completed', 'model_unsupported', 'not_exercised')


def validate_catalog(catalog):
    """Missing model self-check coverage is an infrastructure error."""
    if not isinstance(catalog, list) or not catalog:
        raise ValueError('Missing behavior case catalog')
    if any(not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id']
           for c in catalog):
        raise ValueError('Invalid case identity')
    if len({c['id'] for c in catalog}) != len(catalog):
        raise ValueError('Duplicate case ID')
    if any(c.get('scope') not in ('core', 'diagnostic', 'internal') for c in catalog):
        raise ValueError('Unknown case scope')
    for scope in ('core', 'internal'):
        if not any(c['scope'] == scope for c in catalog):
            raise ValueError('Missing required case scope: ' + scope)


def resources(root, task):
    return owned(task.suite["adapters"]["tock/" + task.bus]["directory"])


def identity_files(root, task):
    paths = list(Path(__file__).parent.glob("*.py"))
    paths += [owned(task.suite["oracle"]), owned(task.suite["cases"]),
              task.path, owned(task.spec["target"]["profile"]),
              owned(task.spec["evaluation"]["suite"]), root / "third_party/sources.lock"]
    for directory in (resources(root, task), root / "benchmark/common/evaluation/tock/support"):
        paths.extend(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {str(p.relative_to(root)): digest(p) for p in paths}


def evaluate_driver(task, candidate: Path, artifact_root: Path, workdir: Path) -> dict:
    with interruption_handler():
        return _evaluate_driver(task, candidate, artifact_root, workdir)


def _evaluate_driver(task, candidate: Path, artifact_root: Path, workdir: Path) -> dict:
    device = task.key
    root, workdir = artifact_root.resolve(), workdir.absolute()
    if workdir.exists():
        raise ValueError('Use a fresh v2 behavior output directory')
    workdir.mkdir(parents=True)
    report = {'schema': 'driverbench.behavior_evaluation/v2', 'device': device,
              'status': 'infrastructure_error', 'compiled': False, 'software_behavior_pass': False,
              'cases': [], 'diagnostics': [], 'model_checks': [],
              'hardware_status': 'not_run', 'new_model_calls': 0}
    start = time.monotonic()
    initial_identity = None
    save(workdir/'behavior_report.json', {**report, 'status':'running', 'phase':'preparing'})
    try:
        directory = resources(root, task)
        profile = json.loads((directory/'profile.json').read_text())
        catalog = json.loads((directory/'catalog.json').read_text())['cases']
        validate_catalog(catalog)
        report.update(profile=profile['profile'], limits=profile['limits'],
                      limitations=profile['limitations'],
                      expected_core_count=sum(c['scope']=='core' for c in catalog))
        initial_identity = identity_files(root, task)
        report['evaluator_sha256'] = initial_identity
        report['evaluator_identity'] = map_digest(initial_identity)
        module = profile.get('candidate_module', device)
        if not isinstance(module, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', module):
            raise ValueError('Invalid candidate Rust module')
        # Rust supports both foo.rs and foo/mod.rs. Preserve the physical
        # layout: flattening mod.rs changes #[path] and include! resolution.
        if candidate.is_symlink():
            raise ValueError('Candidate root must not be a symbolic link')
        if not candidate.exists() and candidate.suffix == '.rs':
            alternative = candidate.with_suffix('') / 'mod.rs'
            if alternative.parent.is_symlink():
                raise ValueError('Candidate module directory must not be a symbolic link')
            candidate = alternative
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError('Candidate must have a regular file or directory module root')
        directory_root = candidate.name == 'mod.rs'
        helper = candidate.parent if directory_root else candidate.parent / module
        if helper.is_symlink() or (helper.exists() and not helper.is_dir()):
            raise ValueError('Candidate helpers must be a regular directory')
        if directory_root:
            if candidate.parent.name != module or candidate.parent.with_suffix('.rs').exists():
                raise ValueError('Ambiguous or mismatched candidate module roots')
        elif (helper / 'mod.rs').exists() or (helper / 'mod.rs').is_symlink():
            raise ValueError('Ambiguous candidate module roots')
        report['candidate_sha256'] = digest(candidate)
        report['candidate_source_layout'] = 'directory_module' if directory_root else 'file_module'
        src = workdir / 'src'; src.mkdir()
        if not directory_root:
            shutil.copyfile(candidate, src / (module + '.rs'))
            if digest(src / (module + '.rs')) != report['candidate_sha256']:
                raise ValueError('Candidate changed while being copied')
        if helper.exists():
            # Includes mod.rs for directory roots. file_map rejects every link.
            report['candidate_helpers_sha256'] = file_map(helper)
            shutil.copytree(helper, src / module)
            if file_map(src / module) != report['candidate_helpers_sha256']:
                raise ValueError('Candidate helpers changed while being copied')
        shutil.copyfile(root/'benchmark/common/evaluation/tock/support/native.rs', src/'support.rs')
        shutil.copyfile(root/'benchmark/common/evaluation/tock/support/executor.rs', src/'executor.rs')
        transport = profile.get('transport', 'i2c')
        if transport not in ('i2c', 'spi', 'gpio'):
            raise ValueError('Unknown frozen model transport')
        if transport == 'spi':
            shutil.copyfile(root/'benchmark/common/evaluation/tock/support/native_spi.rs', src/'spi_support.rs')
        for name in profile.get('support_files', []):
            if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*\.rs', name):
                raise ValueError('Invalid device model support file')
            if (src/name).exists() or name in ('main.rs', 'vectors.rs'):
                raise ValueError('Device support file collides with candidate or harness')
            path = directory/name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Missing or linked device model support file')
            shutil.copyfile(path, src/name)
        extensions = profile.get('public_hil_extensions', {})
        if extensions:
            # Candidate imports remain unchanged; this crate re-exports the pinned
            # kernel plus the SAME public interface supplied in the target tree.
            body = 'pub mod hil { pub use tock_kernel::hil::*;\n'
            for name, filename in extensions.items():
                public = (root/filename).read_text()
                body += 'pub mod '+name+' {\n'+'\n'.join(
                    line for line in public.splitlines() if not line.startswith('//!'))+'\n}\n'
            (src/'platform_hil.rs').write_text(body+'}\n')
        harness = (directory / 'harness.rs').read_text()
        if task.bus == "spi":
            from benchmark.common.evaluation.tock.configuration import render_spi
            (src / "task_config.rs").write_text(render_spi(task.requirements))
        (src / "main.rs").write_text(harness)
        spec = importlib.util.spec_from_file_location('autodriver_oracle_'+device, owned(task.suite["oracle"]))
        oracle = importlib.util.module_from_spec(spec); spec.loader.exec_module(oracle)
        vector_text, numeric = oracle.render_vectors(owned(task.suite["cases"]).parent)
        (src/'vectors.rs').write_text(vector_text)
        save(workdir/'numeric_vectors.json', numeric)
        from benchmark.common.evaluation.tock.compile import compile_candidate, execute_case
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'RUSTUP_HOME', 'LANG', 'LC_ALL') if k in os.environ}
        build = compile_candidate(task, src, workdir, profile['limits']['build_seconds'])
        report['build'] = build
        if build['returncode'] != 0:
            if build.get('phase') == 'public_interface' and not build['timed_out']:
                report.update(status='candidate_compile_failed', reason='public_interface_mismatch')
                return report
            report.update(status='not_completed' if build['timed_out'] else ('candidate_compile_failed' if build.get('phase') == 'candidate_compile' else 'infrastructure_error'), reason='host_compile')
            return report
        report['compiled'] = True
        report['executable_sha256'] = digest(workdir/'behavior')
        save(workdir/'behavior_report.json', {**report, 'status':'running', 'phase':'behavior_cases'})
        for case in catalog:
            run = execute_case(workdir/'behavior', case['id'], workdir, env, profile['limits']['case_process_seconds'])
            records = [s.split('\t',4) for s in run['stdout'].splitlines() if s.startswith('CASE\t')]
            item = dict(case)
            if run['timed_out']:
                item.update(status='not_completed', detail='case_process_time_limit')
            elif run['returncode'] != 0 or len(records)!=1 or len(records[0])!=5 or records[0][1:3]!=[case['id'],case['scope']]:
                item.update(status='execution_error', detail='missing_or_invalid_case_record')
            else:
                status, detail = records[0][3:]
                item.update(status=status if status in STATES else 'execution_error', detail=detail)
            item['failure_class'] = next((kind for kind in ('CONFIG','PROTOCOL','CALLBACK','NUMERIC','DATA')
                                          if kind+':' in item.get('detail','')), item['status'])
            item['observations'] = [s for s in run['stdout'].splitlines() if s.startswith('OBS\t')]
            item['execution'] = run
            report[{'core':'cases','diagnostic':'diagnostics','internal':'model_checks'}[case['scope']]].append(item)
        counts = Counter(c['status'] for c in report['cases'])
        report.update(core_counts=dict(counts), passed=counts['pass'], failed=counts['fail'],
                      completed_core_count=counts['pass']+counts['fail'])
        report['pass_fraction_within_observation'] = counts['pass']/report['expected_core_count']
        if any(c['status']!='pass' for c in report['model_checks']):
            report.update(status='infrastructure_error', reason='model_self_check_failed')
        else:
            report['status'] = next((s for s in ('execution_error','fail','not_completed','model_unsupported','not_exercised') if counts[s]), 'pass')
            report['software_behavior_pass'] = (len(report['cases'])==report['expected_core_count']
                                                and all(c['status']=='pass' for c in report['cases']))
        return report
    except KeyboardInterrupt as error:
        report.update(status='interrupted', reason=str(error) or 'KeyboardInterrupt')
        return report
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        report.update(status='infrastructure_error', reason=str(error))
        return report
    finally:
        if initial_identity is not None:
            try: same = identity_files(root, task)==initial_identity
            except (OSError, ValueError): same = False
            if not same:
                report.update(status='infrastructure_error', software_behavior_pass=False,
                              reason='Evaluator changed during evaluation')
        report['elapsed_seconds'] = round(time.monotonic()-start,3)
        save(workdir/'behavior_report.json',report)
