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
_BOOL_WORDS = {"y", "yes", "n", "no", "true", "false", "on", "off"}


_SAFE_WORD = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$")


def _plain_ok(text: str) -> bool:
    # fast lane: a letter-started word of safe characters can be no
    # number, timestamp, or indicator shape — only the reserved words
    # (null/bool) can still reshape, and the set lookup is cheap
    if _SAFE_WORD.match(text) is not None and text[0].isalpha() and \
            text.lower() not in _NULL_WORDS and text.lower() not in _BOOL_WORDS:
        return True
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
    if text.lower() in _NULL_WORDS or text.lower() in _BOOL_WORDS:
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


def _entries(value, emit, blob, depth, sort_keys):
    """Emits build entries (document order) and blob slices; returns
    True on success, False on unsupported nesting depth."""
    if depth > _MAX_DEPTH:
        return False

    def scalar(text, plain):
        buf = text.encode("utf-8")
        emit(F.BUILD_SCALAR,
             F.STYLE_PLAIN if plain else F.STYLE_DOUBLE_QUOTED,
             len(blob), len(buf))
        blob.extend(buf)

    if value is None:
        scalar("null", True)
    elif value is True:
        scalar("true", True)
    elif value is False:
        scalar("false", True)
    elif isinstance(value, int):
        scalar(str(value), True)
    elif isinstance(value, float):
        scalar(_float_text(value), True)
    elif isinstance(value, _dt.datetime):
        scalar(value.isoformat(sep=" "), True)
    elif isinstance(value, _dt.date):
        scalar(value.isoformat(), True)
    elif isinstance(value, str):
        scalar(value, _plain_ok(value))
    elif isinstance(value, dict):
        emit(F.BUILD_MAP, 0, 0, 0)
        items = list(value.items())
        if sort_keys:
            try:
                items.sort(key=lambda kv: (str(type(kv[0])), str(kv[0])))
            except TypeError:
                pass
        for k, v in items:
            # the host's plain-safety decides keys (a '<<' or
            # resolvable-text key must not re-shape or merge on
            # reparse — the C table cannot know the reading schema)
            if not _entries(k, emit, blob, depth + 1, sort_keys):
                return False
            if not _entries(v, emit, blob, depth + 1, sort_keys):
                return False
        emit(F.BUILD_END, 0, 0, 0)
    elif isinstance(value, (list, tuple)):
        emit(F.BUILD_SEQ, 0, 0, 0)
        for item in value:
            if not _entries(item, emit, blob, depth + 1, sort_keys):
                return False
        emit(F.BUILD_END, 0, 0, 0)
    elif isinstance(value, (set, frozenset)):
        emit(F.BUILD_SEQ, 0, 0, 0)
        for item in sorted(value, key=repr):
            if not _entries(item, emit, blob, depth + 1, sort_keys):
                return False
        emit(F.BUILD_END, 0, 0, 0)
    else:
        raise TypeError(f"cannot dump {type(value).__name__!r} safely")
    return True


def dump(value, *, sort_keys: bool = True) -> str:
    """Serialize one Python value as a single YAML document.

    PyYAML safe_dump defaults: block style, keys sorted, unicode
    allowed. Raises TypeError for types with no safe form.
    """
    parts = []
    pack = F.BUILD_ENTRY.pack
    blob = bytearray()
    count = 0

    def emit(op, style, off, ln):
        nonlocal count
        parts.append(pack(op, style, 0, off, ln))
        count += 1

    if not _entries(value, emit, blob, 0, sort_keys):
        raise F.YeptrisError("dump: object nests too deeply")
    doc = F._lib.yeptris_document_new()
    if not doc:
        raise F.YeptrisError("document allocation failed")
    try:
        rc = F._lib.yeptris_document_build(
            doc, b"".join(parts), count, bytes(blob), len(blob)
        )
        if rc != F.OK:
            raise F.YeptrisError(f"document_build failed: {rc}")
        length = F._sz(0)
        out = F._lib.yeptris_serialize(doc, ctypes.byref(length))
        if not out:
            raise F.YeptrisError("serialize failed")
        return F.read_owned(out, length.value).decode("utf-8")
    finally:
        F._lib.yeptris_document_free(doc)
