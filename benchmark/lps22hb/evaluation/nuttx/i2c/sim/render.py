import runpy
from benchmark.common.api import owned


def render(task, oracle, vectors):
    header, _ = runpy.run_path(str(owned(task.suite["adapters"]["nuttx/i2c"]["vectors"])))["render"]()
    cases = [{"id": "numeric/" + v["id"] + "/" + channel, "scope": "core", "category": "common"}
             for v in vectors for channel in ("temperature", "pressure")]
    cases += [{"id": name, "scope": "core", "category": category} for name, category in
              (("data/stable_change", "common"), ("os/lifecycle", "os"),
               ("integrity/transport_error", "common"), ("integrity/partial_recovery", "common"))]
    cases += [{"id": "model/bdu_retention", "scope": "internal", "category": "model"},
              {"id": "diagnostic/clear_first", "scope": "diagnostic", "category": "diagnostic"}]
    return {"vectors.h": header, "scenario.h":
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'}, cases
