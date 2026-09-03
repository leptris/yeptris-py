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


def _scalar_value(text: str, tag_id: int, implicit: bool = False):
    if tag_id == F.TAG_STR and implicit:
        # the C resolver tags only FULL timestamps (Psych's grammar);
        # PyYAML's resolver also accepts date-only forms — a plain,
        # untagged scalar shaped like a date becomes one here. The
        # dumper quotes strings that would re-shape, so round-trips
        # hold.
        ts = _to_timestamp(text)
        if ts is not None:
            return ts
    if tag_id == F.TAG_NULL:
        return None
    if tag_id == F.TAG_BOOL:
        # Psych resolves single-char y/n as bool; PyYAML does not —
        # the Python binding follows PyYAML (the same override
        # yeptris-ruby documents for Psych parity)
        if len(text) == 1:
            return text
        lowered = text.lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        return text
    if tag_id == F.TAG_INT:
        v = _to_int(text)
        return text if v is None else v
    if tag_id == F.TAG_FLOAT:
        v = _to_float(text)
        return text if v is None else v
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


def load_all(yaml, schema: int = F.SCHEMA_11_COMPAT):
    """Every document in the stream, in order."""
    records, arena = F.drain(_as_bytes(yaml), schema)

    docs: list = []
    stack: list = []
    pending_key: list = [None]   # key awaiting its value, per open map
    pending_tag: list = [None]   # the key's tag_id (merge is tag-driven)
    anchors: dict = {}
    merge_targets: dict = {}     # id(fresh container) -> dict to merge into

    for off in range(0, len(records), F.RECORD_SIZE):
        (etype, style, flags, tag_id, _line, _col,
         v_off, v_len, a_off, a_len, _t_off, _t_len) = _RECORD.unpack_from(records, off)
        value = arena[v_off:v_off + v_len] if v_len else b""
        anchor = arena[a_off:a_off + a_len] if a_len else None

        if etype == F.DOCUMENT_START:
            docs.append(None)
        elif etype in (F.SEQUENCE_START, F.MAPPING_START):
            fresh = [] if etype == F.SEQUENCE_START else {}
            if stack:
                _place(stack, pending_key, pending_tag, fresh, merge_targets)
            else:
                docs[-1] = fresh
            if anchor:
                anchors[anchor] = fresh
            stack.append(fresh)
            pending_key.append(None)
            pending_tag.append(None)
        elif etype in (F.SEQUENCE_END, F.MAPPING_END):
            closed = stack.pop()
            pending_key.pop()
            pending_tag.pop()
            target = merge_targets.pop(id(closed), None)
            if target is not None:
                _merge(target, closed)
        elif etype == F.SCALAR:
            text = value.decode("utf-8")
            v = _scalar_value(text, tag_id, bool(flags & F.EF_IMPLICIT))
            if anchor:
                anchors[anchor] = v
            if stack:
                _place(stack, pending_key, pending_tag, v, merge_targets, tag_id)
            else:
                docs[-1] = v
        elif etype == F.ALIAS:
            # the alias NAME lives in the value field (events.h)
            v = anchors.get(value)
            if stack:
                _place(stack, pending_key, pending_tag, v, merge_targets, F.TAG_STR)
            else:
                docs[-1] = v
    return docs


def _place(stack, pending_key, pending_tag, value, merge_targets,
           tag_id=F.TAG_STR) -> None:
    parent = stack[-1]
    if isinstance(parent, list):
        parent.append(value)
        return
    key = pending_key[-1]
    if key is None:
        pending_key[-1] = value
        pending_tag[-1] = tag_id
        return
    pending_key[-1] = None
    if key == "<<" and pending_tag[-1] == F.TAG_MERGE:
        # a resolver-driven merge key; the value may be an inline map
        # that is still EMPTY (children arrive later) — defer until
        # the container closes
        if isinstance(value, (dict, list)) and not value:
            merge_targets[id(value)] = parent
        else:
            _merge(parent, value)
    else:
        parent[key] = value


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


def load(yaml, schema: int = F.SCHEMA_11_COMPAT):
    """The first document of the stream, or None when empty."""
    docs = load_all(yaml, schema)
    return docs[0] if docs else None
