"""The PyYAML-compatible surface: safe_load / safe_load_all / safe_dump.

Semantics target PyYAML's SafeLoader/SafeDumper (YAML 1.1 implicit
typing, timestamps, merge keys); the differential tests pin every
divergence explicitly. `import yeptris.yaml as yaml` and existing
code that calls yaml.safe_load keeps working.
"""

from ._dumper import dump as _dump
from ._loader import load_all as _load_all

__all__ = ["safe_load", "safe_load_all", "safe_dump", "load", "dump"]


def safe_load(stream):
    """First document of the stream (None when empty)."""
    docs = _load_all(stream)
    return docs[0] if docs else None


def safe_load_all(stream):
    """Every document in the stream, in order."""
    return _load_all(stream)


def safe_dump(data, stream=None, *, sort_keys=True, **_ignored):
    """Serialize as one YAML document (PyYAML safe_dump defaults)."""
    text = _dump(data, sort_keys=sort_keys)
    if stream is None:
        return text
    stream.write(text)
    return None


load = safe_load
dump = safe_dump
