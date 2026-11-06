import json


def render(task, oracle, vectors):
    rows = [f'{{{json.dumps("numeric/" + v["id"])}, {v["temperature"]}, {v["humidity"]}, '
            f'{v["expected_temperature"] / 100:.2f}f, {v["expected_humidity"] / 100:.2f}f}}'
            for v in vectors]
    header = ('static const struct {const char *id;unsigned temperature,humidity;'
              'float expected_temperature,expected_humidity;} vectors[] = {\n'
              + ',\n'.join(rows) + '\n};\n'
              '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n')
    cases = [{"id": "numeric/" + v["id"], "scope": "core", "category": "common"} for v in vectors]
    cases += [{"id": "data/stable_change", "scope": "core", "category": "common"},
              {"id": "os/lifecycle", "scope": "core", "category": "os"},
              {"id": "integrity/invalid_acquisition", "scope": "core", "category": "common"},
              {"id": "diagnostic/no_response", "scope": "diagnostic", "category": "diagnostic"}]
    return {"scenario.h": header}, cases
