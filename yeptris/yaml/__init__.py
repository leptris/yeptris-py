"""The PyYAML-compatible surface: safe_load / safe_load_all / safe_dump.

Semantics target PyYAML's SafeLoader/SafeDumper (YAML 1.1 implicit
typing, timestamps, merge keys); the differential tests pin every
divergence explicitly. `import yeptris.yaml as yaml` and existing
code that calls yaml.safe_load keeps working.

The error taxonomy mirrors PyYAML's import graph: YAMLError and the
per-stage subclasses (ParserError, ComposerError, ConstructorError,
RepresenterError, ...) are exported HERE and from their stage modules
(`from yeptris.yaml.parser import ParserError`), so PyYAML-style
`except yaml.YAMLError` and `except yaml.parser.ParserError` both
work. Every stage error also catches as the legacy yeptris.ParseError.
"""

from .._dumper import dump as _dump
from .._loader import load_all as _load_all
from .._errors import (  # noqa: F401 — the taxonomy, re-exported
    ComposerError,
    ConstructorError,
    EmitterError,
    MarkedYAMLError,
    ParserError,
    ReaderError,
    RepresenterError,
    ScannerError,
    YAMLError,
)

__all__ = [
    "safe_load", "safe_load_all", "safe_dump", "load", "dump",
    "YAMLError", "MarkedYAMLError",
    "ReaderError", "ScannerError", "ParserError", "ComposerError",
    "ConstructorError", "RepresenterError", "EmitterError",
]


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
