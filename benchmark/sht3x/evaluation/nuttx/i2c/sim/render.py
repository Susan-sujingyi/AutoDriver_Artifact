import json


def render(task, oracle, vectors):
    rows, cases = [], []
    for v in vectors:
        name = "numeric/" + v["scope"] + "/" + v["id"]
        rows.append("{" + json.dumps(name) + ", {" + ','.join(map(str, v["bytes"]))
                    + "}, {" + ','.join(float(x / 100).hex() for x in v["lower"])
                    + "}, {" + ','.join(float(x / 100).hex() for x in v["upper"]) + "}}")
        cases.append({"id": name, "scope": "core", "category": "common"})
    cases += [{"id": name, "scope": "core", "category": category} for name, category in
              (("data/stable_change", "common"), ("os/lifecycle", "os"),
               ("integrity/transport_error", "common"))]
    cases += [{"id": f"integrity/crc_byte_{i}", "scope": "core", "category": "common"}
              for i in range(6)]
    cases += [{"id": "model/conversion", "scope": "internal", "category": "model"}]
    return {"scenario.h":
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            'static const struct {const char *id;uint8_t bytes[6];double low[2],high[2];} vectors[] = {\n'
            + ',\n'.join(rows) + '\n};\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'}, cases
