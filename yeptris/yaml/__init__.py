"""The PyYAML-compatible surface: safe_load / safe_load_all /
safe_dump / safe_dump_all.

Semantics target PyYAML's SafeLoader/SafeDumper (YAML 1.1 implicit
typing, timestamps, merge keys); the differential tests pin every
divergence explicitly. `import yeptris.yaml as yaml` and existing
code that calls yaml.safe_load keeps working.

The stream face is PyYAML's law: load_all/safe_load_all are LAZY
generators — the call itself never parses, and a bad document raises
at ITERATION time; empty documents yield None ('---' with no
content) and a zero-document stream ('', comments only) yields
nothing. safe_load answers the FIRST document only, silently — the
binding's documented divergence (PyYAML raises ComposerError on
multi-document input). One pinned divergence remains on the stream
face: the engine parses a stream in one bulk drain, so an invalid
stream raises at the first iteration with NO documents yielded
(PyYAML's per-document parser yields the good prefix first).

The error taxonomy mirrors PyYAML's import graph: YAMLError and the
per-stage subclasses (ParserError, ComposerError, ConstructorError,
RepresenterError, ...) are exported HERE and from their stage modules
(`from yeptris.yaml.parser import ParserError`), so PyYAML-style
`except yaml.YAMLError` and `except yaml.parser.ParserError` both
work. Every stage error also catches as the legacy yeptris.ParseError.
"""

from .._dumper import dump as _dump
from .._dumper import dump_all as _dump_all
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
    "safe_load", "safe_load_all", "safe_dump", "safe_dump_all",
    "load", "load_all", "dump", "dump_all",
    "YAMLError", "MarkedYAMLError",
    "ReaderError", "ScannerError", "ParserError", "ComposerError",
    "ConstructorError", "RepresenterError", "EmitterError",
]


def safe_load(stream):
    """First document of the stream (None when empty)."""
    docs = _load_all(stream)
    return docs[0] if docs else None


def safe_load_all(stream):
    """Every document in the stream, in order — PyYAML's lazy
    generator: nothing parses (and nothing raises) until iteration
    starts, empty documents yield None, a zero-document stream yields
    nothing."""
    yield from _load_all(stream)


def safe_dump(data, stream=None, *, sort_keys=True, **_ignored):
    """Serialize as one YAML document (PyYAML safe_dump defaults)."""
    text = _dump(data, sort_keys=sort_keys)
    if stream is None:
        return text
    stream.write(text)
    return None


def safe_dump_all(documents, stream=None, *, sort_keys=True,
                  explicit_start=False):
    """Serialize documents as one multi-document YAML stream (PyYAML's
    safe_dump_all face): '---' markers between documents (and before
    the first when explicit_start); zero documents serialize as ''.
    With a stream, writes and returns None; without, returns the
    text."""
    text = _dump_all(documents, sort_keys=sort_keys,
                     explicit_start=explicit_start)
    if stream is None:
        return text
    stream.write(text)
    return None


load = safe_load
load_all = safe_load_all
dump = safe_dump
dump_all = safe_dump_all
