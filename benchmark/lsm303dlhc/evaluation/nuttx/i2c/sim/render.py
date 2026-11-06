import json


def render(task, oracle, vectors):
    rows, cases = [], []
    for vector in vectors:
        name = "numeric/" + vector["id"]
        raw = vector["xyz_counts"]
        expected = [float(v / 1000).hex() for v in oracle["ideal"](raw)]
        rows.append("{" + json.dumps(name) + ", {" + ','.join(map(str, raw)) +
                    "}, {" + ','.join(expected) + "}}")
        cases.append({"id": name, "scope": "core", "category": "common"})
    cases += [{"id": name, "scope": "core", "category": category} for name, category in
              (("data/stable_change", "common"), ("os/lifecycle", "os"),
               ("integrity/transport_error", "common"), ("integrity/partial_read", "common"),
               ("data/coherence", "common"))]
    cases += [{"id": "model/frame_lock", "scope": "internal", "category": "model"}]
    scales = [float(v / 1000).hex() for v in oracle["ideal"]([1, 1, 1])]
    return {"scenario.h":
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            'static const double scale[3]={' + ','.join(scales) + '};\n'
            'static const struct {const char *id;int16_t raw[3];double expected[3];} vectors[] = {\n'
            + ',\n'.join(rows) + '\n};\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'}, cases
