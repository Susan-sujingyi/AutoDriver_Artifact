"""Reject obsolete project names in newly installed target material."""
import re


OLD_NAME = re.compile(r"crossos", re.IGNORECASE)


def check_text(name, text):
    if OLD_NAME.search(name) or OLD_NAME.search(text):
        raise ValueError("Obsolete target naming in " + name)


def check_changes(root, before, after):
    for name, metadata in after.items():
        if before.get(name) == metadata:
            continue
        path = root / name
        if path.is_file():
            check_text(name, path.read_bytes().decode("utf-8", errors="replace"))
