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

The validation face (engine TODO.validate.md item 6) rides here too:
validate(data, json_schema=...) / is_valid(...) answer the WHOLE
report (Diagnostic dataclasses: path segments, keyword, message,
line, col), compile_schema() returns the compiled economy's reusable
Schema handle, and load/safe_load take json_schema=/validate= (raise
ValidationError, or collect=True for (document, diagnostics)).
"""

from .. import _ffi as _F
from .. import _validate
from .._dumper import dump as _dump
from .._dumper import dump_all as _dump_all
from .._errors import (  # noqa: F401 — the taxonomy, re-exported
    ComposerError,
    ConstructorError,
    EmitterError,
    MarkedYAMLError,
    MarkedYAMLFError,
    ParserError,
    ReaderError,
    RepresenterError,
    ScannerError,
    ValidationError,
    YAMLError,
)
from .._loader import _as_bytes
from .._loader import load_all as _load_all
from .._validate import (  # noqa: F401 — the validation face
    Diagnostic,
    Schema,
    compile_schema,
    is_valid,
    validate,
)
from .._loader import each_document as _each_document

__all__ = [
    "safe_load", "safe_load_all", "safe_dump", "safe_dump_all",
    "load", "load_all", "dump", "dump_all", "each_document",
    "YAMLError", "MarkedYAMLError", "MarkedYAMLFError",
    "ReaderError", "ScannerError", "ParserError", "ComposerError",
    "ConstructorError", "RepresenterError", "EmitterError",
    "ValidationError",
    "validate", "is_valid", "compile_schema", "Schema", "Diagnostic",
]


def safe_load(stream, *, json_schema=None, validate=False, collect=False):
    """First document of the stream (None when empty).

    With `json_schema=` (a str/bytes JSON Schema document, or a
    compiled `Schema`) or `validate=True` (the schema-family walk:
    explicit-tag consistency + canonical-form key uniqueness), the
    WHOLE stream validates before the answer: an invalid stream
    raises ValidationError carrying every diagnostic (`.diagnostics`),
    or — with `collect=True` — returns `(document, diagnostics)`.
    """
    if json_schema is None and not validate:
        if collect:
            raise TypeError("collect=True requires json_schema or validate=True")
        docs = _load_all(stream)
        return docs[0] if docs else None
    data = _as_bytes(stream)
    diags = _validate.validate(
        data, json_schema=json_schema,
        schema=None if json_schema is not None else _F.SCHEMA_12_CORE)
    if diags and not collect:
        raise _validation_error(diags)
    docs = _load_all(data)
    doc = docs[0] if docs else None
    return (doc, diags) if collect else doc


def _validation_error(diags):
    return ValidationError(
        "the stream does not validate: %d diagnostic%s (first: %s %s)"
        % (len(diags), "" if len(diags) == 1 else "s",
           diags[0].keyword, diags[0].pointer or "(root)"),
        diags)


def safe_load_all(stream, *, json_schema=None, validate=False):
    """Every document in the stream, in order — PyYAML's lazy
    generator: nothing parses (and nothing raises) until iteration
    starts, empty documents yield None, a zero-document stream yields
    nothing.

    With `json_schema=` (str/bytes, or a compiled `Schema`) or
    `validate=True`, the WHOLE stream validates at first iteration
    (the laziness law holds: the call itself still never parses); an
    invalid stream raises ValidationError carrying every diagnostic,
    and no documents yield."""
    if json_schema is None and not validate:
        yield from _load_all(stream)
        return
    data = _as_bytes(stream)
    diags = _validate.validate(
        data, json_schema=json_schema,
        schema=None if json_schema is not None else _F.SCHEMA_12_CORE)
    if diags:
        raise _validation_error(diags)
    yield from _load_all(data)


def each_document(stream, *, schema=_F.SCHEMA_11_COMPAT, max_depth=0):
    """The tolerant per-document walk (py#168): a lazy generator of
    (document, error) pairs in stream order — (document, None) for
    every valid document, (None, error) for every failed one. A
    parse-class failure's error carries the failed document's OWN
    line/column and its stream index, and the walk CONTINUES at the
    next line-start marker (the valid documents after a bad one are
    kept — load_all/safe_load_all raise instead and lose the stream).
    The stream-fatal classes (memory/encoding) yield their error and
    end the walk. `schema` is the resolve-schema id
    (F.SCHEMA_11_COMPAT default, F.SCHEMA_12_CORE for the 1.2 core
    resolver) and rides every frame; `max_depth` bounds nesting per
    frame. Frames materialize through the same drain as load_all: one
    typing SSOT. Requires an engine with the stream iterator's slice
    accessor; ImportError on older libraries."""
    yield from _each_document(stream, schema=schema, max_depth=max_depth)


def safe_dump(data, stream=None, *, sort_keys=True, width=None, indent=None,
              explicit_start=False, default_flow_style=False, **_ignored):
    """Serialize as one YAML document (PyYAML safe_dump defaults).

    width rides the engine's emit options (best_width: flow
    collections wrap past it). indent and default_flow_style are
    accepted for PyYAML call compatibility but carry today: the
    engine's emit options have no indent field, and the dump face is
    block-only (both carried engine gaps, pinned by tests — never a
    silent `_ignored` swallow)."""
    text = _dump(data, sort_keys=sort_keys, width=width,
                 explicit_start=explicit_start)
    if stream is None:
        return text
    stream.write(text)
    return None


def safe_dump_all(documents, stream=None, *, sort_keys=True,
                  explicit_start=False, width=None, indent=None,
                  default_flow_style=False, **_ignored):
    """Serialize documents as one multi-document YAML stream (PyYAML's
    safe_dump_all face): '---' markers between documents (and before
    the first when explicit_start); zero documents serialize as ''.
    With a stream, writes and returns None; without, returns the
    text. width/indent/default_flow_style ride safe_dump's contract."""
    text = _dump_all(documents, sort_keys=sort_keys,
                     explicit_start=explicit_start, width=width)
    if stream is None:
        return text
    stream.write(text)
    return None


load = safe_load
load_all = safe_load_all
dump = safe_dump
dump_all = safe_dump_all
