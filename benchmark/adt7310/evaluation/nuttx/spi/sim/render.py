"""Generate SIM scenarios from the sensor's shared independent oracle."""
import json


def render(task, oracle, vectors):
    params = task.requirements["connection"]["spi"]
    rows = [f'  {{{json.dumps("numeric/" + v["id"])}, {v["raw_count"]}, '
            f'{float(oracle["ideal"](v["raw_count"]) / 100).hex()}f}}'
            for v in vectors]
    text = (f'#define BENCH_SPI_MODE SPIDEV_MODE{params["mode"]}\n'
            f'#define BENCH_SPI_MAX_HZ {params["max_frequency_hz"]}\n'
            'static const struct {const char *id;int16_t raw;float expected;} vectors[] = {\n'
            + ',\n'.join(rows) + '\n};\n'
            '#define BENCH_VECTOR_COUNT (sizeof(vectors) / sizeof(vectors[0]))\n')
    cases = [{"id": "numeric/" + v["id"], "scope": "core", "category": "common"}
             for v in vectors]
    cases += [{"id": "data/stable_change", "scope": "core", "category": "common"},
              {"id": "os/lifecycle", "scope": "core", "category": "os"},
              {"id": "integrity/invalid_acquisition", "scope": "core", "category": "common"}]
    return {"scenario.h": text}, cases
