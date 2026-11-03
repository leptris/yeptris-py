"""Python values to YAML: one flat build spec, ONE FFI call.

The dump-side mirror of the loader's bulk drain: the tree is walked
into a flat entry array (document order — the same grammar as the
event stream) plus one string blob; yeptris_document_build raises the
DOM in a single call, and the emitter serializes it. Per-node FFI is
gone: dump cost is O(chunks), like load.

A string dumps PLAIN exactly when it would reparse as the same
string under the loading schema — every resolvable word, number
shape, and timestamp needs quotes.
"""

from __future__ import annotations

import ctypes
import datetime as _dt

from . import _ffi as F
from ._loader import _to_float, _to_int, _to_timestamp

# PyYAML's null/bool resolvers (case variants via lower())
_NULL_WORDS = {"", "~", "null"}
_NEEDS_ESCAPES = __import__("re").compile(r"[\x00-\x08\x0b-\x1f\x7f\u0085\u2028\u2029]")


def _quote_style(text: str) -> int:
    """PyYAML's emitter rule: an ambiguous scalar takes SINGLE quotes
    when single-quoting can represent it (no control characters, no
    line breaks), double quotes only when escapes are required."""
    if "\n" in text or "\t" in text or _NEEDS_ESCAPES.search(text):
        return F.STYLE_DOUBLE_QUOTED
    return F.STYLE_SINGLE_QUOTED


# PyYAML's resolver set (yaml/resolver.py): single-letter y/n are NOT
# bools there (Psych's rule, which ruby pins, includes them) — the
# dump side must quote exactly what PyYAML's would re-shape
_BOOL_WORDS = {"yes", "no", "true", "false", "on", "off"}


_SAFE_WORD = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_\-./ ]*$")


def _plain_ok(text: str, _match=_SAFE_WORD.match, _null=_NULL_WORDS, _bools=_BOOL_WORDS) -> bool:
    # fast lane: a letter-started word of safe characters (spaces
    # allowed, no trailing space — it would be eaten on reparse) can
    # be no number, timestamp, or indicator shape; only the reserved
    # words can still reshape, and the set lookups are cheap
    if _match(text) is not None:
        if text.endswith(" "):
            return False
        low = text.lower()
        return low not in _null and low not in _bools
    if text != text.strip() or not text:
        return False
    if "\n" in text or "\t" in text:
        return False
    first = text[0]
    if first in "#,[]{}&*!|>'\"%@`":
        return False
    if first in "-?:" and (len(text) == 1 or text[1] in " \t"):
        return False
    if ": " in text or text.endswith(":") or " #" in text:
        return False
    if text == "<<":
        return False
    low = text.lower()
    if low in _NULL_WORDS or low in _BOOL_WORDS:
        return False
    if _to_int(text) is not None or _to_float(text) is not None:
        return False
    if _to_timestamp(text) is not None:
        return False
    return True


def _float_text(value: float) -> str:
    if value != value:
        return ".nan"
    if value == float("inf"):
        return ".inf"
    if value == float("-inf"):
        return "-.inf"
    return repr(value)


_MAX_DEPTH = 500


def _entries(value, emit, blob, depth, sort_keys, quote_strings=False):
    """Emits build entries (document order) and blob slices; returns
    True on success, False on unsupported nesting depth. The scalar
    arms are fully inlined — one function call per node was a
    measurable fraction of the walk in CPython."""
    if depth > _MAX_DEPTH:
        return False

    if value is None:
        emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), 4)
        blob += b"null"
    elif value is True:
        emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), 4)
        blob += b"true"
    elif value is False:
        emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), 5)
        blob += b"false"
    elif type(value) is int:
        buf = str(value).encode()
        emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), len(buf))
        blob += buf
    elif type(value) is float:
        buf = _float_text(value).encode()
        emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), len(buf))
        blob += buf
    elif type(value) is str:
        buf = value.encode("utf-8")
        emit(F.BUILD_SCALAR,
             F.STYLE_DOUBLE_QUOTED if quote_strings
             else (F.STYLE_PLAIN if _plain_ok(value) else _quote_style(value)),
             len(blob), len(buf))
        blob += buf
    elif isinstance(value, (bytes, bytearray)):
        # PyYAML's represent_bytes: strict base64 + the !!binary tag +
        # literal style (its exact emitted form: `!!binary |` + body)
        import base64

        # PyYAML pads the base64 with a trailing newline (its
        # represent_scalar style='|'), which selects the clip chomp —
        # match its exact bytes
        buf = base64.b64encode(bytes(value)) + b"\n"
        emit(F.BUILD_SCALAR, F.STYLE_LITERAL, len(blob), len(buf))
        blob += buf
        # the engine's tag contract: hosts pass the FULL CORE URI —
        # the emitter resolves it to the compact !!binary shorthand
        # itself (a literal "!!binary" payload reads as a LOCAL tag
        # named "!binary" and re-emits percent-escaped, the engine's
        # shorthand-collision law)
        tag = b"tag:yaml.org,2002:binary"
        emit(F.BUILD_TAG, 0, len(blob), len(tag))
        blob += tag
    elif isinstance(value, _dt.datetime):
        buf = value.isoformat(sep=" ").encode()
        emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), len(buf))
        blob += buf
    elif isinstance(value, _dt.date):
        buf = value.isoformat().encode()
        emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), len(buf))
        blob += buf
    elif isinstance(value, dict):
        if type(value) is not dict:
            # PyYAML's representers key on EXACT type, and SafeDumper
            # registers none for dict subclasses: an OrderedDict (or
            # Counter, or defaultdict) hits represent_undefined and
            # raises. The old isinstance arm dumped one as a PLAIN
            # sorted map — silently discarding the very order the type
            # exists to carry, the same silent re-shape py#165 fixed
            # for sets. (PyYAML's full Dumper maps OrderedDict to
            # !!omap; the safe surface has no such representer.)
            raise F.RepresenterError(
                f"cannot dump {type(value).__name__!r} safely")
        emit(F.BUILD_MAP, 0, 0, 0)
        items = list(value.items())
        if sort_keys:
            if all(type(k) is str for k, _ in items):
                items.sort()
            else:
                try:
                    items.sort(key=lambda kv: (str(type(kv[0])), str(kv[0])))
                except TypeError:
                    pass
        for k, v in items:
            # the host's plain-safety decides keys (a '<<' or
            # resolvable-text key must not re-shape or merge on
            # reparse — the C table cannot know the reading schema).
            # The str arm is inlined: one frame per key was a measured
            # fifth of the walk in CPython.
            if type(k) is str:
                buf = k.encode("utf-8")
                emit(F.BUILD_SCALAR,
                     F.STYLE_DOUBLE_QUOTED if quote_strings
                     else (F.STYLE_PLAIN if _plain_ok(k) else _quote_style(k)),
                     len(blob), len(buf))
                blob += buf
            elif not _entries(k, emit, blob, depth + 1, sort_keys, quote_strings):
                return False
            if not _entries(v, emit, blob, depth + 1, sort_keys, quote_strings):
                return False
        emit(F.BUILD_END, 0, 0, 0)
    elif isinstance(value, (list, tuple)):
        emit(F.BUILD_SEQ, 0, 0, 0)
        for item in value:
            if not _entries(item, emit, blob, depth + 1, sort_keys, quote_strings):
                return False
        emit(F.BUILD_END, 0, 0, 0)
    elif isinstance(value, set):
        # PyYAML's represent_set: a !!set-tagged mapping with null
        # values (not a list — the list form reads back as a list and
        # silently re-shapes the data). Items ride the mapping-KEY
        # placement (the host's plain-safety decides each), each
        # followed by an explicit null value. The TAG lands directly
        # after the BUILD_MAP it tags (#300's last-placed law — after
        # BUILD_END the last-placed node would be the last VALUE, and
        # the build's one-trailing-tag admission is scalar-roots-only).
        emit(F.BUILD_MAP, 0, 0, 0)
        tag = b"tag:yaml.org,2002:set"
        emit(F.BUILD_TAG, 0, len(blob), len(tag))
        blob += tag
        for item in sorted(value, key=repr):
            if type(item) is str:
                buf = item.encode("utf-8")
                emit(F.BUILD_SCALAR,
                     F.STYLE_DOUBLE_QUOTED if quote_strings
                     else (F.STYLE_PLAIN if _plain_ok(item) else _quote_style(item)),
                     len(blob), len(buf))
                blob += buf
            elif not _entries(item, emit, blob, depth + 1, sort_keys, quote_strings):
                return False
            emit(F.BUILD_SCALAR, F.STYLE_PLAIN, len(blob), 4)
            blob += b"null"
        emit(F.BUILD_END, 0, 0, 0)
    else:
        # PyYAML parity: a frozenset has no SafeRepresenter (its
        # represent_undefined raises), and neither do str/int
        # SUBCLASSES — the exact-type arms above skip them, so they
        # land here (RepresenterError subclasses TypeError too: the
        # dumper's pre-taxonomy contract, which existing code catches)
        raise F.RepresenterError(
            f"cannot dump {type(value).__name__!r} safely")
    return True


def build_document(value, *, sort_keys: bool = True, schema: int | None = None,
                   quote_strings: bool = False):
    """Build the document handle for one Python value (caller frees).

    The build entries emit the same op stream dump() serializes; the
    CBOR surface reuses it and encodes the document instead.
    schema: SCHEMA_11_COMPAT types the document's plain scalars through
    the psych-compatible resolver (the py-equivalent of the plain-
    safety law), so a host str kept plain ("1e0") stays a CBOR text
    string instead of resolving as a float (#293).
    quote_strings: every host str rides DOUBLE_QUOTED regardless of
    plain-safety — the JSON dump lane's law (yeptris-py#141): JSON
    strings are quoted on the wire anyway, and a quoted scalar never
    resolves, so no resolvable-shaped string ("1e0", "yes") can
    re-shape. Byte-identical to the Ruby binding's dump, which quotes
    every String.
    """
    pack_into = F.BUILD_ENTRY.pack_into
    ent_size = F.BUILD_ENTRY.size
    est = 64
    ents = bytearray(est * ent_size)
    cursor = 0
    blob = bytearray()

    def emit(op, style, off, ln):
        nonlocal cursor, est
        if cursor >= est:
            est *= 2
            new = bytearray(est * ent_size)
            new[:cursor * ent_size] = ents[:cursor * ent_size]
            ents[:] = new
        pack_into(ents, cursor * ent_size, op, style, 0, off, ln)
        cursor += 1

    if not _entries(value, emit, blob, 0, sort_keys, quote_strings):
        raise F.EmitterError("dump: object nests too deeply")
    count = cursor
    if schema is not None and hasattr(F._lib, "yeptris_document_new_schema"):
        doc = F._lib.yeptris_document_new_schema(int(schema))
    else:
        doc = F._lib.yeptris_document_new()
    if not doc:
        raise F.EmitterError("document allocation failed")
    # one copy per buffer: the memoryview slice is zero-copy, the
    # bytes() materialization is the single copy the FFI needs (the
    # old ents[:n] slice + bytes() pair made it two)
    rc = F._lib.yeptris_document_build(
        doc, bytes(memoryview(ents)[:count * ent_size]), count,
        bytes(blob), len(blob)
    )
    if rc != F.OK:
        F._lib.yeptris_document_free(doc)
        raise F.EmitterError(f"document_build failed: {rc}")
    return doc


def dump_all(values, *, sort_keys: bool = True, explicit_start: bool = False,
             width: int | None = None) -> str:
    """Serialize Python values as one multi-document YAML stream.

    '---' markers separate the documents (and precede the first when
    explicit_start); zero documents serialize as ''. The dump/load
    mirror of load_all: dump_all(load_all(x)) round-trips. `width`
    rides the engine's emit options (see dump).
    """
    parts: list = []
    for i, value in enumerate(values):
        if explicit_start or i:
            parts.append("---\n")
        parts.append(dump(value, sort_keys=sort_keys, width=width))
    return "".join(parts)


def dump(value, *, sort_keys: bool = True, width: int | None = None,
         explicit_start: bool = False) -> str:
    """Serialize one Python value as a single YAML document.

    PyYAML safe_dump defaults: block style, keys sorted, unicode
    allowed. Raises TypeError for types with no safe form.

    width: the engine's best_width (13B) via yeptris_serialize_ex —
    flow collections wrap after a value once the line exceeds it.
    The dump face emits BLOCK style only, where the engine does not
    fold, so the output is width-invariant today (pinned); the emit
    options are bound so the face rides the serializer's width knob
    the day flow emission lands. width <= 0 is rejected (the engine
    maps a non-positive width to its default 80 — a silent surprise
    PyYAML's width=0-infinite callers must not eat).
    """
    if width is not None and width < 1:
        raise ValueError("width must be a positive integer")
    doc = build_document(value, sort_keys=sort_keys)
    try:
        length = F._sz(0)
        if width is not None and F.EMIT_OPTIONS_AVAILABLE:
            opts = F._EmitOptions(ctypes.sizeof(F._EmitOptions), 0,
                                  int(width), 0)
            out = F._lib.yeptris_serialize_ex(doc, ctypes.byref(opts),
                                              ctypes.byref(length))
        else:
            out = F._lib.yeptris_serialize(doc, ctypes.byref(length))
        if not out:
            raise F.EmitterError("serialize failed")
        text = F.read_owned(out, length.value).decode("utf-8")
    finally:
        F._lib.yeptris_document_free(doc)
    if explicit_start:
        # the dump_all separator law ('---\n'), not PyYAML's
        # same-line '--- 1' form — one marker style per binding
        text = "---\n" + text
    return text
