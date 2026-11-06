"""Method-side public contract. No reference/evaluator imports are permitted."""
import hashlib
import json
import os
from pathlib import Path
import yaml


class UnsupportedMethod(RuntimeError):
    pass


def validate_live(method, request):
    # Reuse the input identity checks, without pretending to run the algorithm.
    run_contract(method, dict(request, operation="offline_contract"))
    if request.get("operation") != "run":
        raise ValueError("Unsupported method operation")
    if not all(request.get(k) for k in ("model", "budget")) or type(request.get("repetition")) is not int:
        raise ValueError("Live execution needs explicit model, budget and repetition")
    if not all(os.environ.get(k) for k in ("AUTODRIVER_BOUNDED_GATEWAY_URL", "AUTODRIVER_GATEWAY_TOKEN")):
        raise UnsupportedMethod("Live execution requires the isolated host gateway")
    return Path(request["workspace"]).resolve(strict=True)


def run_contract(method, request):
    if request.get("schema") != "autodriver.method-request/v1":
        raise ValueError("Unsupported method request")
    agent = Path(request["workspace"]).resolve(strict=True)
    task = yaml.safe_load((agent / "task.yaml").read_text())
    materials = json.loads((agent / "materials.json").read_text())
    if task["schema"] != "autodriver.public-task/v1" or task["task_id"] != request["task_id"]:
        raise ValueError("Public task identity mismatch")
    if task["condition"] != request["condition"] or materials["task_id"] != task["task_id"]:
        raise ValueError("Public input condition mismatch")
    if request.get("operation") != "offline_contract":
        if not all(request.get(k) for k in ("model", "budget")) or not isinstance(request.get("repetition"), int):
            raise ValueError("Live execution needs explicit model, budget and repetition")
        raise UnsupportedMethod(method + ": live algorithm adaptation to the new public contract is not released")
    result = {"schema": "autodriver.method-result/v1", "method": method,
              "task_id": task["task_id"], "condition": task["condition"],
              "status": "offline_contract_validated", "model_calls": 0,
              "candidate": "target", "collection": "host_canonical_patch",
              "scope": "Public input and final-source handoff only; no generated solution or performance claim"}
    return result
