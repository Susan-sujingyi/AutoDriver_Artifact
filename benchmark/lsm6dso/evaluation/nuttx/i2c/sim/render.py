import json


def render(task, oracle, vectors):
    rows, cases = [], []
    for vector in vectors:
        name = "numeric/" + vector["id"]
        raw = [vector["raw"][axis] for axis in ("x", "y", "z")]
        expected = [float(oracle["ideal_axis"](v) / 1000000).hex() for v in raw]
        rows.append("{" + json.dumps(name) + ", {" + ','.join(map(str, raw)) +
                    "}, {" + ','.join(expected) + "}}")
        cases.append({"id": name, "scope": "core", "category": "common"})
    cases += [{"id": name, "scope": "core", "category": category} for name, category in
              (("data/stable_change", "common"), ("os/lifecycle", "os"),
               ("integrity/transport_error", "common"), ("data/coherence", "common"))]
    cases += [{"id": f"initialization/bank_{bank}", "scope": "core", "category": "common"} for bank in (64, 128)]
    cases += [{"id": "integrity/partial_read", "scope": "core", "category": "common"}]
    cases += [{"id": "model/pair_bdu", "scope": "internal", "category": "model"}]
    return {"scenario.h":
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            f'#define BENCH_ACCEL_SCALE {float(oracle["ideal_axis"](1) / 1000000).hex()}\n'
            'static const struct {const char *id;int16_t raw[3];double expected[3];} vectors[] = {\n'
            + ',\n'.join(rows) + '\n};\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'}, cases
