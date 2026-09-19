# frozen from the TODO.cbor/04 surface: the Python face of the CBOR
# (RFC 8949) codec. Encode rides the same document build the YAML
# dumper uses, then the C encoder; decode materializes through the
# document's YAML serialization (the same DOM, so semantics match —
# the direct records path is TODO.cbor/05+ follow-up work).

from __future__ import annotations

import ctypes

from . import _dumper
from . import _ffi as F

STRICT = 1  # decode: reject non-minimal length arguments
CANONICAL = 2  # encode: RFC 8949 s4.2.1 core deterministic profile

__all__ = ["STRICT", "CANONICAL", "available", "load", "load_all", "dump", "dump_all"]


def available() -> bool:
    return bool(F.CBOR_AVAILABLE)


def _require() -> None:
    if not F.CBOR_AVAILABLE:
        raise F.YeptrisError("the vendored libyeptris has no CBOR support")


def _decode_doc(data: bytes, strict: bool):
    st = ctypes.c_int(0)
    doc = F._lib.yeptris_cbor_decode(data, len(data), STRICT if strict else 0, ctypes.byref(st))
    if not doc:
        msg, _line, _col = F.last_error()
        raise F.ParseError(msg)
    return doc


def load(data, strict: bool = False):
    """Decode ONE CBOR data item into Python (the JSON data model)."""
    _require()
    data = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    doc = _decode_doc(data, strict)
    try:
        length = F._sz(0)
        out = F._lib.yeptris_serialize(doc, ctypes.byref(length))
        if not out:
            raise F.YeptrisError("serialize failed")
        text = F.read_owned(out, length.value).decode("utf-8")
    finally:
        F._lib.yeptris_document_free(doc)
    from . import yaml as _yaml  # the compat_11 materializer

    return _yaml.load(text)


def load_all(data, strict: bool = False) -> list:
    """Decode a CBOR Sequence (RFC 8742): every item, in order."""
    _require()
    data = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    items: list = []
    pending: list = []

    cb_type = getattr(F, "_CBOR_ITEM_CB", None)
    if cb_type is None:
        raise F.YeptrisError("the vendored libyeptris has no CBOR support")

    @cb_type
    def _cb(_ctx, item, _index):
        try:
            length = F._sz(0)
            out = F._lib.yeptris_serialize(item, ctypes.byref(length))
            if not out:
                raise F.YeptrisError("serialize failed")
            text = F.read_owned(out, length.value).decode("utf-8")
            from . import yaml as _yaml

            items.append(_yaml.load(text))
        except Exception as e:  # noqa: BLE001 — aborts the C iteration
            pending.append(e)
            return 1
        return 0

    st = F._lib.yeptris_cbor_decode_sequence(
        data, len(data), STRICT if strict else 0, _cb, None, None
    )
    if pending:
        raise pending[0]
    if st == 0 and data:
        msg, _line, _col = F.last_error()
        raise F.ParseError(msg)
    return items


def dump(value, *, canonical: bool = True) -> bytes:
    """Encode one Python value as a single CBOR item (canonical by
    default: minimal lengths, definite lengths, bytewise-sorted keys)."""
    _require()
    # the build walk's key order IS the document's pair order: sorted
    # for canonical, the dict's insertion order otherwise
    doc = _dumper.build_document(value, sort_keys=canonical)
    try:
        length = F._sz(0)
        buf = F._lib.yeptris_cbor_encode(doc, CANONICAL if canonical else 0, ctypes.byref(length))
        if not buf:
            msg, _line, _col = F.last_error()
            raise F.YeptrisError(msg or "cbor encode failed")
        return F.read_owned(buf, length.value)
    finally:
        F._lib.yeptris_document_free(doc)


def dump_all(values, *, canonical: bool = True) -> bytes:
    """Encode Python values as a CBOR Sequence (RFC 8742)."""
    _require()
    values = list(values)
    docs = [_dumper.build_document(v, sort_keys=canonical) for v in values]
    try:
        arr = (ctypes.c_void_p * len(docs))(*docs)
        length = F._sz(0)
        buf = F._lib.yeptris_cbor_encode_sequence(
            arr, len(docs), CANONICAL if canonical else 0, ctypes.byref(length)
        )
        if not buf:
            msg, _line, _col = F.last_error()
            raise F.YeptrisError(msg or "cbor encode failed")
        return F.read_owned(buf, length.value)
    finally:
        for d in docs:
            F._lib.yeptris_document_free(d)
