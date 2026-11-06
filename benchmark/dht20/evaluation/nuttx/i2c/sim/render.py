import json


def render(task, oracle, vectors):
    rows, cases = [], []
    for v in vectors:
        name = "numeric/" + v["id"]
        exact = oracle["ideal"](v["raw"])
        values = [exact[k] / 100 for k in
                  ("temperature_centiCelsius", "humidity_hundredths_percent")]
        rows.append("{" + json.dumps(name) + ", {" + ','.join(map(str, v["raw_bytes"]))
                    + "}, {" + ','.join(float(x).hex() for x in values) + "}}")
        cases.append({"id": name, "scope": "core", "category": "common"})
    cases += [{"id": name, "scope": "core", "category": category} for name, category in
              (("data/stable_change", "common"), ("os/lifecycle", "os"),
               ("integrity/transport_error", "common"),
               ("initialization/recovery", "common"),
               ("initialization/uncalibrated", "common"))]
    cases += [{"id": f"timing/conversion_{n}", "scope": "core", "category": "common"}
              for n in (80000, 100000, 150000)]
    cases += [{"id": f"integrity/crc_{byte}_{bit}", "scope": "core", "category": "common"}
              for byte in range(7) for bit in range(8)]
    cases += [{"id": "device/permanent_busy", "scope": "diagnostic", "category": "common"},
              {"id": "model/protocol", "scope": "internal", "category": "model"}]
    return {"scenario.h":
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            'static const struct {const char *id;uint8_t bytes[7];double expected[2];} vectors[] = {\n'
            + ',\n'.join(rows) + '\n};\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'}, cases
