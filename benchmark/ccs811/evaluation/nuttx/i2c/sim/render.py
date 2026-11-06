import json


def render(task, oracle, vectors):
    params = task.requirements["connection"]["i2c"]
    rows = []
    cases = [{"id": "model/acquisition_history", "scope": "internal", "category": "model"}]
    for v in vectors:
        co2, tvoc = v["raw"]["co2"], v["raw"]["tvoc"]
        cid = ("numeric/" if v["in_output_range"] else "range/") + v["id"]
        computed = v["independently_computed"]
        rows.append('{' + json.dumps(cid) + f', {{{co2}, {tvoc}}}, '
                    + '{' + str(int(400 <= co2 <= 29206)) + ', ' + str(int(0 <= tvoc <= 32768)) + '}, '
                    + '{' + float(computed["co2_ppm"]).hex() + ', '
                    + (computed["tvoc_ppb"] / 1000).hex() + '}}')
        cases.append({"id": cid, "scope": "core", "category": "common"})
    header = (f'#define BENCH_I2C_ADDRESS {params["address_7bit"]}\n'
              # The frozen public driver_api.h fixes this board bus to 100 kHz.
              '#define BENCH_I2C_MAX_HZ 100000\n'
              'static const struct {const char *id;uint16_t words[2];bool valid[2];'
              'double expected[2];} vectors[] = {\n' + ',\n'.join(rows) + '\n};\n'
              '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n')
    cases += [{"id": cid, "scope": "core", "category": "os" if cid.startswith("os/") else "common"}
              for cid in ("data/stable_change", "os/lifecycle", "integrity/invalid_acquisition", "integrity/transport_error")]
    return {"scenario.h": header}, cases
