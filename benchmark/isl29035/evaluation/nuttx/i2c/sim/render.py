import json


def render(task, oracle, vectors):
    rows, cases = [], []
    for vector in vectors:
        name = "numeric/" + vector["id"]
        rows.append("{" + json.dumps(name) + f', {vector["raw"]["light"]}, '
                    + f'{vector["lower"]}, {vector["upper"]}' + "}")
        cases.append({"id": name, "scope": "core", "category": "common"})
    cases += [{"id": name, "scope": "core", "category": category} for name, category in
              (("data/stable_change", "common"), ("os/lifecycle", "os"),
               ("integrity/transport_error", "common"))]
    cases += [{"id": "model/conversion", "scope": "internal", "category": "model"}]
    return {"scenario.h":
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            'static const struct {const char *id;uint8_t raw;float low,high;} vectors[] = {\n'
            + ',\n'.join(rows) + '\n};\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'}, cases
