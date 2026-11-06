"""Expand experiment definitions; reject incomplete live execution settings."""
import argparse
import json
from benchmark.common.api import ROOT, load, owned, task_paths, digest, save, relative


def expand(spec, live=False):
    if spec.get('methods') != ['autodriver']:
        raise ValueError('This artifact includes AutoDriver only')
    if spec.get('shared_framework_plan'):
        raise ValueError('Standalone runs recover fresh per-trial framework profiles')
    available = task_paths()
    variant_name=spec.get('variant','full')
    from method.autodriver.implementation.system.variants import variant
    variant(variant_name)
    if variant_name!='full' and spec['methods']!=['autodriver']:
        raise ValueError('A mechanism intervention requires one autodriver-only experiment')
    cases = spec.get("cases")
    if cases is not None:
        if not isinstance(cases, list) or not cases:
            raise ValueError("Explicit experiment cases must be nonempty")
        keys = [(c["method"], c["task_id"], c["condition"]) for c in cases]
        if len(keys) != len(set(keys)) or any(c[0] not in spec["methods"] for c in keys):
            raise ValueError("Duplicate case or undeclared method")
        tasks = sorted({c["task_id"] for c in cases})
    else:
        tasks = sorted(available) if spec["tasks"] == "all44" else spec["tasks"]
    if set(tasks) - set(available):
        raise ValueError("Experiment contains unknown tasks")
    if live and (not isinstance(spec.get("repetitions"), int) or spec["repetitions"] < 1
                 or not spec.get("model") or not spec.get("budget")):
        raise ValueError("Set repetitions, model and budget before starting experiments")
    rows = []
    for name in spec["methods"]:
        method = load(ROOT / "method" / name / "method.yaml")
        allowed_state = method["live_execution"] == "available"
        if live and not allowed_state:
            raise ValueError("Method capability is not released for live execution: " + name)
        for task in tasks:
            conditions = spec.get("method_conditions", {}).get(name, spec.get("conditions", []))
            if cases is not None:
                conditions = [c["condition"] for c in cases if c["method"] == name and c["task_id"] == task]
                if not conditions:
                    continue
            if not conditions:
                raise ValueError("Explicit conditions required for method: " + name)
            if name=='autodriver' and (live or variant_name!='full') and conditions!=['SD']:
                raise ValueError('New autodriver experiments require SD only')
            if live and (not isinstance(method["supported"]["live"],dict)
                         or set(conditions) - set(method["supported"]["live"]["conditions"])):
                raise ValueError("Unsupported live condition for method: " + name)
            for condition in conditions:
                if condition not in ("S", "D", "SD"):
                    raise ValueError("Unknown input condition")
                for repeat in range(spec.get("repetitions") or 1):
                    row={"method": name, "task_id": task, "condition": condition,
                         "repeat": repeat, "adapter_status": method["offline_contract"]}
                    if name=='autodriver':row['variant']=variant_name
                    rows.append(row)
    return {"experiment": spec["name"], "status": "planned_only", "trials": rows, "model_calls": 0}


def execute_experiment(spec, batch, *, output_root="result", resume=False,
                       allow_budget_change=False, verification_group=None):
    from tests.common.campaign import execute
    rows = expand(spec, live=True)["trials"]
    return execute(ROOT, spec, batch, rows,
                   output_root=output_root, retry_infrastructure=resume,
                   allow_budget_change=allow_budget_change, verification_group=verification_group)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--batch")
    parser.add_argument("--output-root", default="result")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-budget-change", action="store_true")
    args = parser.parse_args()
    if args.execute:
        if not args.batch:
            parser.error("--execute requires --batch")
        result = execute_experiment(load(owned(args.config)), args.batch,
                                    output_root=args.output_root, resume=args.resume,
                                    allow_budget_change=args.allow_budget_change)
    else:
        result = expand(load(owned(args.config)), args.live)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
