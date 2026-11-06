"""Host-only credential loading; never search outside the delivery root."""
import shlex
from pathlib import Path
from urllib.parse import urlsplit


def load_host(root):
    path = Path(root) / ".env"
    if path.is_symlink():
        raise ValueError("Host configuration must be a regular file")
    if path.stat().st_mode & 0o077:
        raise ValueError("Host configuration must have mode 0600")
    needed = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw = line.partition("=")
        key = key.strip()
        if key not in needed:
            continue
        # Parse quoting only. Never expand variables, source a shell, or print
        # malformed values (which may contain the credential).
        try:
            parts = shlex.split(raw, comments=True)
        except ValueError:
            raise ValueError("Malformed host configuration field: " + key) from None
        if not separator or len(parts) != 1 or key in values:
            raise ValueError("Invalid or duplicate host configuration field: " + key)
        values[key] = parts[0]
    if any(not values.get(k) for k in needed):
        raise ValueError("Missing host API configuration")
    if (values['OPENAI_API_KEY'] == 'REPLACE_WITH_YOUR_API_KEY'
            or values['OPENAI_MODEL'] == 'YOUR_MODEL'
            or '.invalid' in values['OPENAI_BASE_URL']):
        raise ValueError('Configure your own provider/model credentials in .env before live execution')
    provider = urlsplit(values["OPENAI_BASE_URL"])
    if (provider.scheme != "https" or not provider.hostname
            or provider.username or provider.password or provider.query or provider.fragment):
        raise ValueError("Invalid fixed upstream URL")
    return {k: values[k] for k in needed}


def public_host_config(config):
    return {"model": config["OPENAI_MODEL"], "provider": config["OPENAI_BASE_URL"],
            "credentials": "host_only"}
