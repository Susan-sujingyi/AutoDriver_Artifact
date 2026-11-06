import json


def render(task, oracle, vectors):
    rows, cases = [], []
    for vector in vectors:
        name = "numeric/" + vector["id"]
        rows.append("{" + json.dumps(name) + ", " + str(vector["raw_bytes"][0]) + "}")
        cases.append({"id": name, "scope": "core", "category": "common"})
    cases += [{"id": name, "scope": "core", "category": category} for name, category in
              (("numeric/all_counts", "common"), ("data/stable_change", "common"),
               ("os/lifecycle", "os"), ("integrity/transport_error", "common"),
               ("protocol/unused_status_bits", "common"))]
    cases += [{"id": "model/proximity_state", "scope": "internal", "category": "model"}]
    return {"scenario.h":
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            'static const struct {const char *id;uint8_t raw;} vectors[] = {\n'
            + ',\n'.join(rows) + '\n};\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'
            'static const uint8_t expected_raw[256]={' +
            ','.join(str(oracle["decode_proximity"](i)) for i in range(256)) + '};\n'}, cases
