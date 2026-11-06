"""Read only explicitly declared, regular public target interfaces."""
from pathlib import Path, PurePosixPath


def read_interfaces(workspace, task):
    workspace = Path(workspace).resolve()
    names = task["target"]["interface_files"]
    if not isinstance(names, list) or not names:
        raise ValueError("Missing required public interface list")
    result = []
    seen = set()
    for name in names:
        if (not isinstance(name, str) or "\\" in name or
                not name.startswith("target/") or
                any(p in ("", ".", "..") for p in name.split("/")) or
                PurePosixPath(name).is_absolute()):
            raise ValueError("Invalid public interface path")
        path = workspace
        for part in name.split("/"):
            path = path / part
            if path.is_symlink():
                raise ValueError("Linked public interface is prohibited: " + name)
        if not path.is_file() or not path.resolve().is_relative_to(workspace / "target"):
            raise ValueError("Missing or escaped public interface: " + name)
        if name not in seen:
            result.append((name, path.read_text()))
            seen.add(name)
    return result
