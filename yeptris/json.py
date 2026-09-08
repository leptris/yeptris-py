"""Strict JSON (RFC 8259) — json.loads-compatible (TODO.restructure/41).

Two engines, one contract:

1. the native materializer (``yeptris._native`` — opt-in build): the
   fused scan-kernel descent, the Ruby win's shape
2. the strict-parse ctypes walk: ``yeptris_parse_json`` is the
   validator gate, then the columnar value stream converts under
   JSON rules — in strict JSON every PLAIN scalar record can only be
   a degraded number (real strings are quoted), so int64-overflow
   integers rebuild exactly from their text.

Hook arguments (parse_float, parse_int, parse_constant, object_hook,
object_pairs_hook) delegate to stdlib json.loads: hooks are stdlib
machinery and the fast paths exist for the default conversions.
"""

from __future__ import annotations

import json as _stdlib_json
import struct
from json import JSONDecodeError  # noqa: F401 — the surface's error type

try:
    from . import _native as _ext
except ImportError:
    _ext = None

from . import _ffi as F
from ._loader import (
    _V_ALIAS,
    _V_ANCHOR,
    _V_BOOL,
    _V_CLOSE,
    _V_DOC,
    _V_FLOAT,
    _V_INT,
    _V_MAP,
    _V_NULL,
    _V_SEQ,
    _V_STR,
)

_BITS_TO_DOUBLE = struct.Struct("<q").pack, struct.Struct("<d").unpack


class _WalkError(Exception):
    """The gate passed but the stream shape is not JSON — ours, not
    the input's; reported as an invalid-JSON error all the same."""

    def __init__(self, pos: int):
        super().__init__(pos)
        self.pos = pos


def _json_str(tag: int, plain: int, off: int, ln: int, arena: bytes):
    text = arena[off:off + ln] if ln else b""
    if tag == F.TAG_INT:
        return int(text, 10)
    if tag == F.TAG_FLOAT:
        return float(text)
    if tag == F.TAG_NULL:
        return None
    if tag == F.TAG_BOOL:
        return text == b"true"
    if plain:
        # strict JSON passed the gate: an unquoted scalar the resolver
        # left a string is an integer beyond int64 — exact from its
        # text (json.loads' behavior)
        return int(text, 10)
    return text.decode("utf-8")


def _place(stack: list, pending: list, v) -> bool:
    """Place a completed value; True when it landed at top level."""
    if not stack:
        return True
    parent = stack[-1]
    if type(parent) is list:
        parent.append(v)
    else:
        key = pending[-1]
        if key is None:
            pending[-1] = v
        else:
            pending[-1] = None
            parent[key] = v
    return False


def _walk_columns(b: bytes):
    kinds, tags, _is_keys, bools, offs, lens, pays, arena, closer = F.drain_columns(
        b, F.SCHEMA_12_CORE)
    try:
        root = None
        placed = False
        stack: list = []
        pending: list = [None]
        for i, kind in enumerate(kinds):
            if kind == _V_DOC or kind == _V_ANCHOR:
                continue
            if kind == _V_STR:
                v = _json_str(tags[i], bools[i], offs[i], lens[i], arena)
            elif kind == _V_INT:
                v = pays[i]
            elif kind == _V_FLOAT:
                v = _BITS_TO_DOUBLE[1](_BITS_TO_DOUBLE[0](pays[i]))[0]  # payload is the double's int64 bits
            elif kind == _V_BOOL:
                v = bools[i] == 1
            elif kind == _V_NULL:
                v = None
            elif kind == _V_MAP or kind == _V_SEQ:
                v = {} if kind == _V_MAP else []
                _place(stack, pending, v)
                stack.append(v)
                pending.append(None)
                continue
            elif kind == _V_CLOSE:
                v = stack.pop()
                pending.pop()
                if not stack:
                    root, placed = v, True
                continue
            else:  # _V_ALIAS: strict JSON has none
                raise _WalkError(i)
            if _place(stack, pending, v):
                root, placed = v, True
        if not placed:
            raise _WalkError(0)
        return root
    finally:
        closer()


def _fallback_loads(s):
    b = s.encode("utf-8") if isinstance(s, str) else s
    try:
        F.parse_json_strict(b)  # the gate: anything RFC 8259 rejects is rejected
    except F.ParseError:
        # json.loads' non-standard constants (NaN/Infinity by default)
        # fail the RFC gate — delegate exactly that quirk: stdlib accepts
        # only this extension beyond the gate, so gate-reject +
        # stdlib-accept is precisely the constant case
        try:
            return _stdlib_json.loads(s)
        except ValueError:
            raise JSONDecodeError("invalid JSON", "", 0) from None
    try:
        return _walk_columns(b)
    except _WalkError as e:
        raise JSONDecodeError("invalid JSON", "", e.pos) from None


def loads(s, *, parse_float=None, parse_int=None, parse_constant=None,
          object_hook=None, object_pairs_hook=None):
    if parse_float or parse_int or parse_constant or object_hook or object_pairs_hook:
        return _stdlib_json.loads(
            s, parse_float=parse_float, parse_int=parse_int,
            parse_constant=parse_constant, object_hook=object_hook,
            object_pairs_hook=object_pairs_hook)
    if _ext is not None:
        return _ext.loads(s)
    return _fallback_loads(s)


def engine() -> str:
    """Which engine loads() uses — evidence for the smoke/profile."""
    return "native" if _ext is not None else "strict-ffi"


__all__ = ["loads", "engine", "JSONDecodeError"]
