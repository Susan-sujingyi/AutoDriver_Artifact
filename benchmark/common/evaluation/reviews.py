"""Host-only, identity-bound explanations of reviewed historical failures.

Reviews do not define correctness, change a backend verdict, or search source
text for bugs. A finding is reusable only for the exact candidate, evaluator,
and observed case. Historical evidence paths are provenance, never inputs.
"""
from copy import deepcopy
from pathlib import Path
import re

from benchmark.common.api import identity, load, digest

REGISTRY = Path(__file__).parent / "reviews" / "registry.json"
POINTER = re.compile(r"\b(buffer|tx|rx)=(0x[0-9a-fA-F]+)\b")


def witness(case):
    """Remove ASLR addresses while retaining pointer equality and event order."""
    pointers = {}

    def replace(match):
        address = int(match[2], 16)
        value = "null" if address == 0 else pointers.setdefault(address, "buffer" + str(len(pointers)))
        return match[1] + "=" + value

    run = case.get("execution", {})
    return identity({
        "schema": "autodriver.case-witness/v1",
        **{key: case.get(key) for key in ("id", "scope", "status", "detail", "failure_class")},
        "observations": [POINTER.sub(replace, line) for line in case.get("observations", [])],
        "returncode": run.get("returncode"), "timed_out": run.get("timed_out"),
        "stdout": POINTER.sub(replace, run.get("stdout", "")),
        "stderr": POINTER.sub(replace, run.get("stderr", "")),
    })


def context(task_id, behavior):
    return {"task_id": task_id,
            "candidate_sha256": behavior.get("candidate_sha256"),
            "candidate_source_layout": behavior.get("candidate_source_layout"),
            "candidate_helpers_sha256": behavior.get("candidate_helpers_sha256", {}),
            "evaluator_identity": behavior.get("evaluator_identity")}


def matched_reviews(report, *, registry=REGISTRY):
    """Return report pointers and findings; ambiguous reviews fail closed."""
    if (report.get("candidate_integrity") != "verified" or
            report.get("native_build", {}).get("status") != "pass"):
        return {}
    behavior = report.get("behavior", {})
    if not behavior.get("compiled") or not behavior.get("evaluator_identity"):
        return {}
    checks = behavior.get("model_checks", [])
    if not checks or any(c.get("status") != "pass" for c in checks):
        return {}
    document = load(registry)
    if document.get("schema") != "autodriver.reviewed-causes/v1":
        raise ValueError("Unsupported reviewed-cause registry")
    matches = {}
    expected = context(report["task_id"], behavior)
    for entry in document["reviews"]:
        if entry["context"] != expected:
            continue
        for i, case in enumerate(behavior.get("cases", [])):
            if (case.get("scope") != "core" or case.get("status") == "pass" or
                    entry["cases"].get(case.get("id")) != witness(case)):
                continue
            pointer = f"/behavior/cases/{i}"
            if pointer in matches:
                raise ValueError("Ambiguous reviewed cause for " + pointer)
            matches[pointer] = {"review_id": entry["id"],
                                "registry_sha256": digest(registry),
                                "context": deepcopy(expected), "witness_sha256": witness(case),
                                "reason_code": entry["reason_code"],
                                "responsibility": entry["responsibility"],
                                "confidence": "confirmed",
                                "causal_chain": deepcopy(entry["causal_chain"]),
                                "provenance": deepcopy(entry["provenance"])}
    return matches


def annotate(result, matches):
    """Enrich a chosen observation; preserve outcomes and scoring precedence."""
    result = deepcopy(result)
    found = [matches[e["pointer"]] for e in result.get("evidence", [])
             if e.get("path") == "report.json" and e.get("pointer") in matches]
    if len(found) != 1:
        return result
    review = found[0]
    result["observation_reason_code"] = result["reason_code"]
    result.update({key: review[key] for key in ("reason_code", "responsibility", "confidence")})
    result["review"] = review
    result["causal_chain"] = [{"stage": "behavior_execution", "description": step}
                              for step in review["causal_chain"]]
    result["recovery"] = {"available": False, "action": "retain_candidate_failure",
                          "stage": "behavior_execution", "prerequisites": [],
                          "automatic_generation_retry": False}
    return result
