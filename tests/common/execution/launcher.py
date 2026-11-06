"""Single-threaded child bootstrap; no model key or evaluator material is read."""
import argparse
import json
import os
from pathlib import Path
import resource

from .linux import restrict_files
from .transport import install_transport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--control-fd", type=int)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    restrict_files(config["readonly"], config["writable"])
    install_transport(args.control_fd)
    os.chdir(config["cwd"])
    os.execvpe(config["command"][0], config["command"], config["environment"])


if __name__ == "__main__":
    main()
