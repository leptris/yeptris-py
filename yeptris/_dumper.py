"""Python values to YAML: build a document, serialize once.

The DOM mutation API is the single building seam (the one the Ruby
dumper rides). A string dumps PLAIN exactly when it would reparse as
the same string under the loading schema — every resolvable word,
number shape, and timestamp needs quotes.
"""

from __future__ import annotations

import ctypes
import datetime as _dt

from . import _ffi as F
from ._loader import _to_float, _to_int, _to_timestamp

# PyYAML's null/bool resolvers (case variants via lower())
_NULL_WORDS = {"", "~", "null"}
_BOOL_WORDS = {"y", "yes", "n", "no", "true", "false", "on", "off"}


def _plain_ok(text: str) -> bool:
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


def _scalar_node(doc, text: str, plain: bool):
    buf = text.encode("utf-8")
    node = F._lib.yeptris_node_new_scalar(
        doc, buf, len(buf), F.STYLE_PLAIN if plain else F.STYLE_DOUBLE_QUOTED
    )
    if not node:
        raise F.YeptrisError("node allocation failed")
    return node


_MAX_DEPTH = 500


def _build(doc, value, sort_keys, depth):
    if depth > _MAX_DEPTH:
        raise F.YeptrisError("dump: object nests too deeply")
    if value is None:
        return _scalar_node(doc, "null", True)
    if value is True:
        return _scalar_node(doc, "true", True)
    if value is False:
        return _scalar_node(doc, "false", True)
    if isinstance(value, int):
        return _scalar_node(doc, str(value), True)
    if isinstance(value, float):
        return _scalar_node(doc, _float_text(value), True)
    if isinstance(value, _dt.datetime):
        return _scalar_node(doc, value.isoformat(sep=" "), True)
    if isinstance(value, _dt.date):
        return _scalar_node(doc, value.isoformat(), True)
    if isinstance(value, str):
        return _scalar_node(doc, value, _plain_ok(value))
    if isinstance(value, dict):
        node = F._lib.yeptris_node_new_mapping(doc)
        if not node:
            raise F.YeptrisError("node allocation failed")
        items = list(value.items())
        if sort_keys:
            try:
                items.sort(key=lambda kv: (str(type(kv[0])), str(kv[0])))
            except TypeError:
                pass
        for k, v in items:
            vn = _build(doc, v, sort_keys, depth + 1)
            # keys ride pre-built nodes: the host's plain-safety
            # decides (a "<<" or resolvable-text key must not re-shape
            # or merge on reparse — the C table cannot know the
            # reading schema)
            kn = _build(doc, k, sort_keys, depth + 1)
            rc = F._lib.yeptris_node_map_add_node(node, kn, vn)
            if rc != F.OK:
                raise F.YeptrisError(f"map_add failed: {rc}")
        return node
    if isinstance(value, (list, tuple)):
        node = F._lib.yeptris_node_new_sequence(doc)
        if not node:
            raise F.YeptrisError("node allocation failed")
        for item in value:
            if F._lib.yeptris_node_seq_add(
                    node, _build(doc, item, sort_keys, depth + 1)) != F.OK:
                raise F.YeptrisError("seq_add failed")
        return node
    if isinstance(value, (set, frozenset)):
        node = F._lib.yeptris_node_new_sequence(doc)
        for item in sorted(value, key=repr):
            if F._lib.yeptris_node_seq_add(
                    node, _build(doc, item, sort_keys, depth + 1)) != F.OK:
                raise F.YeptrisError("seq_add failed")
        return node
    raise TypeError(f"cannot dump {type(value).__name__!r} safely")


def dump(value, *, sort_keys: bool = True) -> str:
    """Serialize one Python value as a single YAML document.

    PyYAML safe_dump defaults: block style, keys sorted, unicode
    allowed. Raises TypeError for types with no safe form.
    """
    doc = F._lib.yeptris_document_new()
    if not doc:
        raise F.YeptrisError("document allocation failed")
    try:
        root = _build(doc, value, sort_keys, 0)
        if F._lib.yeptris_document_set_root(doc, root) != F.OK:
            raise F.YeptrisError("set_root failed")
        length = F._sz(0)
        out = F._lib.yeptris_serialize(doc, ctypes.byref(length))
        if not out:
            raise F.YeptrisError("serialize failed")
        return out[: length.value].decode("utf-8")
    finally:
        F._lib.yeptris_document_free(doc)
