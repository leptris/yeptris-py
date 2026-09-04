"""Bulk-drain loader: events to Python values.

One drain (records + arena read once), then a pure-Python walk over
the unpacked record array — no per-event FFI calls. Typing comes
from the C resolver's tag_id (the typing SSOT); the conversion
functions below mirror PyYAML's SafeLoader constructors exactly
(float sexagesimal folds reversed digits; timestamps carry their
offset as tzinfo; merge keys are resolver-driven, so a quoted '<<'
is a literal key).
"""

from __future__ import annotations

import datetime as _dt
import struct
import re

from . import _ffi as F

_RECORD = F._RECORD

_BOOL_TRUE = {"yes", "true", "on"}
_BOOL_FALSE = {"no", "false", "off"}

_INT_DEC = re.compile(r"^[-+]?(0|[1-9][0-9_]*)$")
_INT_HEX = re.compile(r"^[-+]?0x[0-9a-fA-F_]+$")
_INT_OCT = re.compile(r"^[-+]?0[0-7_]+$")
_INT_BIN = re.compile(r"^[-+]?0b[01_]+$")
_INT_SEXAGESIMAL = re.compile(r"^[-+]?[1-9][0-9_]*(:[0-5]?[0-9])+$")

# PyYAML's float resolver: the dot is required and the exponent
# carries a mandatory sign — "1e3" is a STRING
_FLOAT = re.compile(
    r"^[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?$"
    r"|^\.[0-9_]+(?:[eE][-+][0-9]+)?$"
    r"|^[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*$"
)
_FLOAT_INF = re.compile(r"^[-+]?\.(?:inf|Inf|INF)$")
_FLOAT_NAN = re.compile(r"^\.(?:nan|NaN|NAN)$")

# PyYAML's timestamp regexp (SafeConstructor.timestamp_regexp)
_TIMESTAMP = re.compile(
    r"""^(?P<year>[0-9][0-9][0-9][0-9])
        -(?P<month>[0-9][0-9]?)
        -(?P<day>[0-9][0-9]?)
        (?:(?:[Tt]|[ \t]+)
        (?P<hour>[0-9][0-9]?)
        :(?P<minute>[0-9][0-9])
        :(?P<second>[0-9][0-9])
        (?:\.(?P<fraction>[0-9]*))?
        (?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9][0-9]?)
        (?::(?P<tz_minute>[0-9][0-9]))?))?)?$""",
    re.VERBOSE,
)


def _to_int(text: str):
    if _INT_DEC.match(text):
        return int(text.replace("_", ""), 10)
    if _INT_HEX.match(text):
        return int(text.replace("_", ""), 16)
    if _INT_OCT.match(text):
        return int(text.replace("_", ""), 8)
    if _INT_BIN.match(text):
        return int(text.replace("_", ""), 2)
    if _INT_SEXAGESIMAL.match(text):
        sign = -1 if text[0] == "-" else 1
        value = 0
        for part in text.lstrip("+-").split(":"):
            value = value * 60 + int(part.replace("_", ""))
        return sign * value
    return None


def _to_float(text: str):
    """PyYAML's construct_yaml_float, faithfully."""
    t = text.replace("_", "").lower()
    sign = -1 if t[0] == "-" else 1
    if t[0] in "+-":
        t = t[1:]
    if t == ".inf":
        return sign * float("inf")
    if t == ".nan":
        return float("nan")
    if ":" in t:
        # reversed digits, base 1, *= 60 — the fraction part rides
        # its segment as a float
        value = 0.0
        base = 1.0
        for part in reversed(t.split(":")):
            value += float(part) * base
            base *= 60
        return sign * value
    if _FLOAT.match(text):
        return sign * float(t)
    return None


def _to_timestamp(text: str):
    m = _TIMESTAMP.match(text)
    if m is None:
        return None
    g = m.groupdict()
    year, month, day = int(g["year"]), int(g["month"]), int(g["day"])
    if not g["hour"]:
        return _dt.date(year, month, day)
    fraction = g["fraction"] or ""
    micro = int(fraction[:6].ljust(6, "0")) if fraction else 0
    tzinfo = None
    if g["tz_sign"]:
        delta = _dt.timedelta(hours=int(g["tz_hour"]),
                              minutes=int(g["tz_minute"] or 0))
        if g["tz_sign"] == "-":
            delta = -delta
        tzinfo = _dt.timezone(delta)
    elif g["tz"] == "Z":
        tzinfo = _dt.timezone.utc
    return _dt.datetime(year, month, day, int(g["hour"]), int(g["minute"]),
                        int(g["second"]), micro, tzinfo=tzinfo)


def _value(value: bytes, tag_id: int, flags: int):
    """A scalar's Python value: byte-level fast paths for the shapes
    that dominate real documents (int()/float() accept bytes), the
    full PyYAML-conversion layer for everything else."""
    if tag_id == F.TAG_STR:
        text = value.decode("utf-8")
        if flags & F.EF_IMPLICIT:
            # the C resolver tags only FULL timestamps (Psych's
            # grammar); PyYAML also accepts date-only forms — a
            # plain scalar shaped like a date becomes one here. The
            # dumper quotes strings that would re-shape.
            ts = _to_timestamp(text)
            if ts is not None:
                return ts
        return text
    if tag_id == F.TAG_INT:
        # digits-only (with optional sign, no leading zero — PyYAML
        # reads those as octal) is the overwhelming shape
        body = value[1:] if value[:1] in (b"-", b"+") else value
        if body.isdigit() and (body[:1] != b"0" or len(body) == 1):
            return int(value)
        text = value.decode("utf-8")
        v = _to_int(text)
        return text if v is None else v
    if tag_id == F.TAG_NULL:
        return None
    if tag_id == F.TAG_FLOAT:
        # the C tag follows the 1.1 grammar; PyYAML additionally
        # requires the dot — with a dot and no sexagesimal ':' the
        # C float() is exact
        if b":" not in value and b"." in value:
            try:
                return float(value)
            except ValueError:
                pass
        text = value.decode("utf-8")
        v = _to_float(text)
        return text if v is None else v
    text = value.decode("utf-8")
    if tag_id == F.TAG_BOOL:
        # Psych resolves single-char y/n as bool; PyYAML does not —
        # this binding follows PyYAML (the override yeptris-ruby
        # documents for Psych parity)
        if len(text) == 1:
            return text
        lowered = text.lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        return text
    if tag_id == F.TAG_TIMESTAMP:
        v = _to_timestamp(text)
        return text if v is None else v
    return text


def _merge(target: dict, source) -> None:
    """'<<' merge: existing keys win; sequences merge in order."""
    if isinstance(source, dict):
        for k, v in source.items():
            target.setdefault(k, v)
    elif isinstance(source, list):
        for item in source:
            if isinstance(item, dict):
                for k, v in item.items():
                    target.setdefault(k, v)


# YeptrisValueKind (values.h)
_V_DOC, _V_NULL, _V_BOOL, _V_INT, _V_FLOAT = 0, 1, 2, 3, 4
_V_STR, _V_TS, _V_SEQ, _V_MAP, _V_CLOSE, _V_ALIAS, _V_ANCHOR = 5, 6, 7, 8, 9, 10, 11


def _cvalue(kind, tag, text: bytes, b: int, pay: int):
    """A columnar scalar's Python value — the same conversion layer
    _value applies, entered with the C-pre-converted payload where
    the fast paths allow it."""
    if kind == _V_STR:
        s = text.decode("utf-8")
        if b == 1:  # implicit-plain: PyYAML's date-only timestamps
            ts = _to_timestamp(s)
            if ts is not None:
                return ts
        return s
    if kind == _V_INT:
        body = text[1:] if text[:1] in (b"-", b"+") else text
        if body.isdigit() and (body[:1] != b"0" or len(body) == 1):
            return pay  # the C conversion already ran
        s = text.decode("utf-8")
        v = _to_int(s)
        return s if v is None else v
    if kind == _V_NULL:
        return None
    if kind == _V_FLOAT:
        if b":" not in text and b"." in text:
            return struct.unpack("<d", struct.pack("<q", pay))[0]
        s = text.decode("utf-8")
        v = _to_float(s)
        return s if v is None else v
    s = text.decode("utf-8")
    if kind == _V_BOOL:
        if len(s) == 1:
            return s
        return True if b == 1 else False
    if kind == _V_TS:
        v = _to_timestamp(s)
        return s if v is None else v
    return s


def load_all_columns(yaml, schema: int = F.SCHEMA_11_COMPAT):
    """Every document, via the columnar value stream (libyeptris >
    0.1.1; feature-detected). The walk is load_all's lockstep twin —
    same placement semantics, fields from tight columns."""
    kinds, tags, is_keys, bools, offs, lens, pays, arena, close = F.drain_columns(
        _as_bytes(yaml), schema)
    try:
        docs: list = []
        stack: list = []
        pending_key: list = [None]
        pending_tag: list = [None]
        anchors: dict = {}
        merge_targets: dict = {}
        pending_anchor = None

        def _place(v, tag):
            if stack:
                parent = stack[-1]
                if type(parent) is list:
                    parent.append(v)
                else:
                    key = pending_key[-1]
                    if key is None:
                        pending_key[-1] = v
                        pending_tag[-1] = tag
                    else:
                        pending_key[-1] = None
                        if key == "<<" and pending_tag[-1] == F.TAG_MERGE:
                            if type(v) in (dict, list) and not v:
                                merge_targets[id(v)] = parent
                            else:
                                _merge(parent, v)
                        else:
                            parent[key] = v
            else:
                docs[-1] = v

        for i in range(len(kinds)):
            kind = kinds[i]
            if kind == _V_STR:
                o, l = offs[i], lens[i]
                text = arena[o:o + l] if l else b""
                v = _cvalue(kind, tags[i], text, bools[i], 0)
                if pending_anchor is not None:
                    anchors[pending_anchor] = v
                    pending_anchor = None
                _place(v, tags[i])
            elif kind == _V_MAP or kind == _V_SEQ:
                fresh = {} if kind == _V_MAP else []
                if pending_anchor is not None:
                    anchors[pending_anchor] = fresh
                    pending_anchor = None
                _place(fresh, F.TAG_STR)
                stack.append(fresh)
                pending_key.append(None)
                pending_tag.append(None)
            elif kind == _V_CLOSE:
                closed = stack.pop()
                pending_key.pop()
                pending_tag.pop()
                target = merge_targets.pop(id(closed), None)
                if target is not None:
                    _merge(target, closed)
            elif kind == _V_DOC:
                docs.append(None)
            elif kind == _V_ALIAS:
                l = lens[i]
                v = anchors.get(arena[offs[i]:offs[i] + l] if l else b"")
                _place(v, F.TAG_STR)
            elif kind == _V_ANCHOR:
                pending_anchor = arena[offs[i]:offs[i] + lens[i]]
            else:
                o, l = offs[i], lens[i]
                text = arena[o:o + l] if l else b""
                v = _cvalue(kind, tags[i], text, bools[i], pays[i])
                if pending_anchor is not None:
                    anchors[pending_anchor] = v
                    pending_anchor = None
                _place(v, tags[i])
        return docs
    finally:
        close()


def load_all(yaml, schema: int = F.SCHEMA_11_COMPAT):
    """Every document in the stream, in order."""
    records, arena = F.drain(_as_bytes(yaml), schema)

    docs: list = []
    stack: list = []
    pending_key: list = [None]   # key awaiting its value, per open map
    pending_tag: list = [None]   # the key's tag_id (merge is tag-driven)
    anchors: dict = {}
    merge_targets: dict = {}     # id(fresh container) -> dict to merge into

    # hot loop: C-level record iteration with the placement logic
    # inlined (this walk is the bulk of load time; a function call
    # per event is measurable in CPython)
    for rec in _RECORD.iter_unpack(records):
        etype = rec[0]
        tag_id = rec[3]
        if etype == F.SCALAR:
            v_off, v_len = rec[6], rec[7]
            value = arena[v_off:v_off + v_len] if v_len else b""
            v = _value(value, tag_id, rec[2])
            a_len = rec[9]
            if a_len:
                anchors[arena[rec[8]:rec[8] + a_len]] = v
            if stack:
                parent = stack[-1]
                if type(parent) is list:
                    parent.append(v)
                else:
                    key = pending_key[-1]
                    if key is None:
                        pending_key[-1] = v
                        pending_tag[-1] = tag_id
                    else:
                        pending_key[-1] = None
                        if tag_id == F.TAG_MERGE:
                            if type(v) in (dict, list) and not v:
                                merge_targets[id(v)] = parent
                            else:
                                _merge(parent, v)
                        else:
                            parent[key] = v
            else:
                docs[-1] = v
        elif etype == F.MAPPING_START or etype == F.SEQUENCE_START:
            fresh = {} if etype == F.MAPPING_START else []
            a_len = rec[9]
            if a_len:
                anchors[arena[rec[8]:rec[8] + a_len]] = fresh
            if stack:
                parent = stack[-1]
                if type(parent) is list:
                    parent.append(fresh)
                else:
                    key = pending_key[-1]
                    if key is None:
                        pending_key[-1] = fresh
                        pending_tag[-1] = F.TAG_STR
                    else:
                        pending_key[-1] = None
                        if key == "<<" and pending_tag[-1] == F.TAG_MERGE and not fresh:
                            merge_targets[id(fresh)] = parent
                        else:
                            parent[key] = fresh
            else:
                docs[-1] = fresh
            stack.append(fresh)
            pending_key.append(None)
            pending_tag.append(None)
        elif etype == F.MAPPING_END or etype == F.SEQUENCE_END:
            closed = stack.pop()
            pending_key.pop()
            pending_tag.pop()
            target = merge_targets.pop(id(closed), None)
            if target is not None:
                _merge(target, closed)
        elif etype == F.DOCUMENT_START:
            docs.append(None)
        elif etype == F.ALIAS:
            # the alias NAME lives in the value field (events.h)
            rec_v_len = rec[7]
            v = anchors.get(arena[rec[6]:rec[6] + rec_v_len] if rec_v_len else b"")
            if stack:
                parent = stack[-1]
                if type(parent) is list:
                    parent.append(v)
                else:
                    key = pending_key[-1]
                    if key is None:
                        pending_key[-1] = v
                        pending_tag[-1] = F.TAG_STR
                    else:
                        pending_key[-1] = None
                        if key == "<<" and pending_tag[-1] == F.TAG_MERGE:
                            _merge(parent, v)
                        else:
                            parent[key] = v
            else:
                docs[-1] = v
    return docs


def _as_bytes(yaml) -> bytes:
    if isinstance(yaml, (bytes, bytearray)):
        return bytes(yaml)
    if isinstance(yaml, str):
        return yaml.encode("utf-8")
    read = getattr(yaml, "read", None)
    if read is None:
        raise TypeError("expected str, bytes, or a file-like object")
    data = read()
    if isinstance(data, str):
        return data.encode("utf-8")
    return bytes(data)


def _load_all_fast(yaml, schema: int = F.SCHEMA_11_COMPAT):
    if F.COLUMNS:
        return load_all_columns(yaml, schema)
    return load_all(yaml, schema)


def load(yaml, schema: int = F.SCHEMA_11_COMPAT):
    """The first document of the stream, or None when empty."""
    docs = _load_all_fast(yaml, schema)
    return docs[0] if docs else None
