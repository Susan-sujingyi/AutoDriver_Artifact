"""Collect regular method logs without following agent-created symbolic links."""
import json
import os
from pathlib import Path
import stat


def read_regular(path):
    """The host must not follow an untrusted output pointer outside its workspace."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Linked method output is not accepted")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Method output must be a regular file")
        return stream.read()


def collection_sources(public, method=None):
    """One allowlist shared by collection and its host recovery manifest."""
    public = Path(public)
    if public.is_symlink():
        raise ValueError("Linked method output directory")
    children = sorted(public.iterdir()) if public.exists() else []
    sources = []
    for source in children:
        if source.is_symlink() or not source.is_file():
            continue
        if source.suffix not in (".json", ".jsonl", ".log", ".txt"):
            continue
        if source.name == "events.jsonl":
            sources.append((source, source.name))
        elif source.name not in ("request.json", "knowledge-binding.json", "method-result.json"):
            sources.append((source, "method/" + source.name))
    for directory in children:
        if directory.is_symlink() or not directory.is_dir():
            continue
        if directory.name != "requests" and not directory.name.startswith("compile-"):
            continue
        for source in sorted(directory.iterdir()):
            if source.is_file() and not source.is_symlink():
                sources.append((source, "method/" + directory.name + "/" + source.name))
    if method=='autodriver':
        system=public/'autodriver'
        if system.is_symlink():raise ValueError('linked autodriver state directory')
        if system.is_dir():
            for base,directories,names in os.walk(system,followlinks=False):
                directories[:]=sorted(n for n in directories if not (Path(base)/n).is_symlink())
                for name in sorted(names):
                    path=Path(base)/name
                    if path.is_file() and not path.is_symlink() and path.suffix in {'.json','.constructor','.md','.txt'}:
                        sources.append((path,'method/system/'+path.relative_to(system).as_posix()))
    return sources


def collect(public, destination, method):
    public, destination = Path(public), Path(destination)
    sources = collection_sources(public, method)
    if not public.exists() and not sources:
        return
    destination.mkdir(parents=True, exist_ok=True)
    logs = destination / "method"
    logs.mkdir(exist_ok=True)
    for source, relative in sources:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Method processes have stopped. NOFOLLOW also protects the final open.
        target.write_bytes(read_regular(source))
