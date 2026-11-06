import runpy
from benchmark.common.api import owned


def render(task, oracle, vectors):
    adapter = task.suite["adapters"]["nuttx/i2c"]
    header = runpy.run_path(str(owned(adapter["vectors"])))["render"]()
    params = task.requirements["connection"]["i2c"]
    configuration = (f'#define BENCH_I2C_ADDRESS {params["address_7bit"]}\n'
                     '#define BENCH_I2C_MAX_HZ 100000\n')
    cases = [{"id": "model/acquisition_history", "scope": "internal", "category": "model"}]
    cases += [{"id": "numeric/" + v["id"], "scope": "core", "category": "common"} for v in vectors]
    cases += [{"id": cid, "scope": "core", "category": "os" if cid.startswith("os/") else "common"}
              for cid in ("data/stable_change", "os/lifecycle", "integrity/invalid_acquisition", "integrity/transport_error")]
    return {"vectors.h": header, "scenario.h": configuration}, cases
