"""Release identity verification. Verification never invokes source compilation."""
from pathlib import Path
import os

from benchmark.common.api import ROOT, digest, identity, inventory, load, owned, save, resolve_task, task_paths
from benchmark.common.source.validate import verify_source
from benchmark.common.hardware.package import firmware_path


from benchmark.common.freeze_scope import owned_files, scopes_record, verify_experiment_release


def validation_identity(task):
    """Bind execution semantics, separately from the whole artifact identity.

    Offline installation and experiment scheduling are release-owned files but
    do not change a candidate's native build or independent behavior model.
    """
    files = task.files()
    for name in ("benchmark/common/api.py", "benchmark/common/execution.py",
                 "benchmark/common/submission.py", "benchmark/common/release.py",
                 "benchmark/common/source/configuration.py",
                 "tests/common/pipeline.py", "tests/common/sandbox.py"):
        files[name] = digest(owned(name))
    for base in (ROOT / "benchmark/common/target", ROOT / "benchmark/common/evaluation",
                 ROOT / "benchmark/common/applications", ROOT / "benchmark/common/hardware",
                 owned(task.spec["evaluation"]["suite"]).parent,
                 owned(task.spec["target"]["profile"]).parent):
        for path in base.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[path.relative_to(ROOT).as_posix()] = digest(path)
    return identity(files)


def verify_release():
    """Verify this extracted snapshot; no prior experiment result is required."""
    result = verify_experiment_release('autodriver')
    result.update(schema='autodriver.standalone-verification/v1',
                  task_count=len(task_paths()), model_calls=0, hardware_operations=0,
                  scope='content integrity; no model performance or hardware claim')
    return result
