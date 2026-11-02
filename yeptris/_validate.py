"""The validation face (engine TODO.validate.md item 6): parse +
validate fused, diagnostics as structured Python objects.

Two doors over one engine pass:

- the SCHEMA-FAMILY walk (`validate(data)` with no schema): explicit-
  tag consistency (spec 10.3 resolution) and mapping-key uniqueness
  (spec 3.2.1.3 — keys compare by canonical form, so 0o13 and 0xB are
  the same key) under the core schema; the spec 10.2 JSON productions
  (every plain scalar a JSON type, string mapping keys) under
  SCHEMA_12_JSON.
- JSON SCHEMA draft 2020-12 (`json_schema=...`): the schema is parsed
  with the same engine; the keyword subset (type/enum/const/pattern/
  numeric and string bounds/items/properties/required/$ref/if-then-
  else/...) is documented in the C header (validate.h). Unknown
  keywords are ignored, per 2020-12.

Every diagnostic is a `Diagnostic` dataclass: the JSON-pointer
instance path split into `path` segments (~0/~1 unescaped), a spec-
spelled `keyword`, the engine's bounded detail as `message`, and the
offending node's 1-based line/column (0 = unknown: scalars whose
bytes the DOM owns have no input position; the path still identifies
them). Results are WHOLE REPORTS: one call enumerates every miss,
never first-fail.

`compile_schema` returns a `Schema` — the 0.6.59 compiled economy:
the schema parses ONCE (retained DOM + compiled pattern programs)
and validates MANY instance streams. One handle per thread (the
document law); immutable after compile, so read-only sharing is
safe; `close()` frees everything it owns in one call.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field

from . import _ffi as F
from ._loader import _as_bytes

__all__ = ["Diagnostic", "Schema", "available", "compile_schema",
           "is_valid", "validate"]


def available() -> bool:
    """Whether the loaded libyeptris carries the validation face
    (libyeptris >= 0.6.59)."""
    return bool(F.VALIDATE_AVAILABLE)


def _require() -> None:
    if not F.VALIDATE_AVAILABLE:
        raise F.YeptrisError(
            "the loaded libyeptris has no validation face; this surface "
            "requires libyeptris >= 0.6.59 (set YEPTRIS_LIB_PATH to a "
            "newer build)")


# YeptrisDiagCode -> the spec-spelled keyword. The family-walk codes
# (1-4) have no JSON Schema keyword; their names spell the check.
_KEYWORDS = {
    1: "tag", 2: "keyType", 3: "jsonType", 4: "duplicateKey",
    5: "type", 6: "required", 7: "enum", 8: "const", 9: "pattern",
    10: "minimum", 11: "maximum", 12: "exclusiveMinimum",
    13: "exclusiveMaximum", 14: "minLength", 15: "maxLength",
    16: "minItems", 17: "maxItems", 18: "uniqueItems",
    19: "additionalProperties", 20: "anyOf", 21: "oneOf", 22: "not",
    23: "$ref", 24: "false", 25: "depth", 26: "limit",
    27: "multipleOf", 28: "minProperties", 29: "maxProperties",
    30: "contains", 31: "minContains", 32: "maxContains",
    33: "dependentRequired",
}


@dataclass
class Diagnostic:
    """One validation miss. `path` holds the JSON-pointer segments
    (~0/~1 unescaped; [] at the document root); `keyword` is the spec
    keyword (or the family-walk check name); `message` is the
    engine's bounded detail; `line`/`col` are the offending node's
    1-based mark (0 = unknown)."""

    code: int
    keyword: str
    path: list = field(default_factory=list)
    message: str = ""
    line: int = 0
    col: int = 0

    @property
    def pointer(self) -> str:
        """The raw JSON-pointer form of `path` ('' at the root)."""
        return "".join(
            "/" + seg.replace("~", "~0").replace("/", "~1")
            for seg in self.path)


def _pointer_segments(raw: bytes) -> list:
    """'/a/b/0' -> ['a', 'b', '0'], with ~1 and ~0 unescaped; '' (the
    root) -> []."""
    text = raw.decode("utf-8", "replace")
    if not text:
        return []
    return [seg.replace("~1", "/").replace("~0", "~")
            for seg in text.split("/")[1:]]


def _drain(status: int, report) -> list:
    """A validate status + report handle -> the diagnostic list (the
    report is always freed; no c_void_p ever escapes)."""
    try:
        if status not in (F.OK, F.ERROR_SCHEMA):
            # the parse itself failed (or a bad argument): the same
            # stage taxonomy every load failure rides
            msg, line, col = F.last_error()
            raise F._stage_error(status, msg or "validation failed",
                                 line, col)
        diags: list = []
        if not report:
            return diags
        n = F._lib.yeptris_validate_report_count(report)
        at = F._lib.yeptris_validate_report_at
        for i in range(n):
            raw = at(report, i)
            if not raw:
                break
            d = ctypes.cast(raw, ctypes.POINTER(F._Diagnostic)).contents
            diags.append(Diagnostic(
                code=int(d.code),
                keyword=_KEYWORDS.get(d.code, "diagnostic"),
                path=_pointer_segments(d.path),
                message=d.detail.decode("utf-8", "replace"),
                line=int(d.line), col=int(d.col),
            ))
        return diags
    finally:
        if report:
            F._lib.yeptris_validate_report_free(report)


def _schema_bytes(schema) -> bytes:
    if isinstance(schema, (bytes, bytearray)):
        return bytes(schema)
    if isinstance(schema, str):
        return schema.encode("utf-8")
    raise TypeError("expected str or bytes for the schema document")


def validate(data, *, json_schema=None, schema: int = F.SCHEMA_12_CORE) -> list:
    """Validate a YAML stream, answering the WHOLE report: every
    diagnostic, in engine order ([] when valid).

    json_schema= (str/bytes schema document, or a compiled `Schema`)
    selects the JSON Schema draft 2020-12 walk. Without it, `schema`
    selects the schema-family walk: SCHEMA_12_CORE (explicit-tag
    consistency + canonical-form key uniqueness) or SCHEMA_12_JSON
    (the spec 10.2 JSON productions).

    A stream that does not parse raises the usual load-stage error
    (ParserError and friends); a $ref that resolves nowhere is a
    diagnostic, not an exception.
    """
    _require()
    data = _as_bytes(data)
    report = F._p()
    if json_schema is not None:
        if isinstance(json_schema, Schema):
            if json_schema._handle is None:
                raise F.YeptrisError("the schema handle is closed")
            st = F._lib.yeptris_schema_validate(
                json_schema._handle, data, len(data), ctypes.byref(report))
        else:
            schema_bytes = _schema_bytes(json_schema)
            st = F._lib.yeptris_json_schema_validate(
                schema_bytes, len(schema_bytes), data, len(data),
                ctypes.byref(report))
    else:
        st = F._lib.yeptris_validate(
            data, len(data), int(schema), ctypes.byref(report))
    return _drain(st, report)


def is_valid(data, *, json_schema=None, schema: int = F.SCHEMA_12_CORE) -> bool:
    """True when `validate` answers no diagnostics."""
    return not validate(data, json_schema=json_schema, schema=schema)


class Schema:
    """A COMPILED JSON Schema (the leptris-XSD economy): parse the
    schema once, validate many streams. Usable as a context manager;
    `close()` frees the handle and everything it owns in one call —
    afterwards the handle is DEAD (passing it anywhere is undefined,
    exactly like a freed document)."""

    _handle = None

    def __init__(self, schema):
        _require()
        schema_bytes = _schema_bytes(schema)
        handle = F._lib.yeptris_schema_compile(schema_bytes,
                                               len(schema_bytes))
        if not handle:
            msg, line, col = F.last_error()
            raise F.ParseError(msg or "schema compilation failed",
                               line, col)
        self._handle = handle

    def validate(self, data) -> list:
        """Every diagnostic across every document of `data` ([] when
        valid); same contract as the module-level `validate`."""
        if self._handle is None:
            raise F.YeptrisError("the schema handle is closed")
        data = _as_bytes(data)
        report = F._p()
        st = F._lib.yeptris_schema_validate(
            self._handle, data, len(data), ctypes.byref(report))
        return _drain(st, report)

    def is_valid(self, data) -> bool:
        return not self.validate(data)

    def close(self) -> None:
        # yeptris_schema_free(NULL) is a documented no-op; freeing
        # twice is guarded here so close() is idempotent
        if self._handle is not None:
            F._lib.yeptris_schema_free(self._handle)
            self._handle = None

    def __enter__(self) -> "Schema":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:  # pragma: no cover - interpreter teardown
            pass


def compile_schema(schema) -> Schema:
    """Compile a JSON Schema document (str/bytes) into a reusable
    `Schema` handle."""
    return Schema(schema)
