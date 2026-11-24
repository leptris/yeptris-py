"""The compact JSON tape (tape.h): the simdjson-class columnar
materialization (TODO.restructure/85).

One fused strict walk appends ONE record per token — no DOM, no links,
no per-value host crossings. Records are three parallel columns carved
from one allocation: the kind byte, and the off/len pair (span-carrying
kinds view the caller's source bytes zero-copy; container links ride
``off`` as the matching CLOSE's record index). Numbers record spans
only — parse validates the grammar (the lean shape scan) and
``Tape.convert`` converts (the deferred contract). Strings stay raw
spans: escapes untouched, exactly as the qbc kernel found them.

The lenient route (``lenient=True``) is simdjson's deferred contract,
opt-in: structural checks stay at parse while NUMBER grammar defers to
convert — ``"1.2.3"`` parses here and rejects at convert/materialize,
exactly where simdjson's number parse would fail.

``Tape.values()`` is the convenience whole-document materialization:
one walk over the copied columns — the columnar face stays the bulk
contract (``kinds``/``offs``/``lens`` are one ``ctypes.string_at``
each), ``values()`` exists so consumers (and the tests) can price the
columnar data against :func:`yeptris.json.loads`.
"""

from __future__ import annotations

import array
import ctypes
import re
from typing import Union

from . import _ffi as F
from ._errors import YeptrisError
from ._loader import _as_bytes

__all__ = ["Tape", "available", "parse_tape"]

_Stream = Union[str, bytes]

# the RFC 8259 number grammar — the lenient NUM record's materialize-
# time classification (the deferred contract's reject lives here when
# the host asks for values; Tape.convert is the raw authority)
_INT_SHAPE = re.compile(rb"-?(?:0|[1-9][0-9]*)\Z")
_FLOAT_SHAPE = re.compile(
    rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)(?:[eE][-+]?[0-9]+)?\Z"
    rb"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?[eE][-+]?[0-9]+\Z")

_SIZE_MAX = 0xFFFFFFFFFFFFFFFF

_SIMPLE_ESCAPES = {
    ord('"'): b'"', ord("\\"): b"\\", ord("/"): b"/",
    ord("b"): b"\b", ord("f"): b"\f", ord("n"): b"\n",
    ord("r"): b"\r", ord("t"): b"\t",
}


def available() -> bool:
    """Whether the loaded libyeptris carries the tape face
    (libyeptris >= 0.6.77)."""
    return bool(F.TAPE_AVAILABLE)


def _require() -> None:
    if not F.TAPE_AVAILABLE:
        raise YeptrisError(
            "the loaded libyeptris has no tape face; this surface "
            "requires libyeptris >= 0.6.77 (set YEPTRIS_LIB_PATH to a "
            "newer build)")


def _unescape(raw: bytes) -> bytes:
    """The tape's string spans keep escapes untouched (the qbc law);
    values() finishes them. \\uXXXX combines surrogate pairs; a lone
    surrogate encodes WTF-8-style via surrogatepass (stdlib json.loads
    tolerates the same input)."""
    if 0x5C not in raw:  # backslash
        return raw
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c != 0x5C:
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:  # unreachable: parse closed the string
            raise ValueError("trailing backslash in a string span")
        simple = _SIMPLE_ESCAPES.get(raw[i + 1])
        if simple is not None:
            out += simple
            i += 2
            continue
        if raw[i + 1:i + 2] == b"u" and i + 6 <= n:
            cp = int(raw[i + 2:i + 6], 16)
            i += 6
            if 0xD800 <= cp <= 0xDBFF and raw[i:i + 2] == b"\\u" and i + 6 <= n:
                lo = int(raw[i + 2:i + 6], 16)
                if 0xDC00 <= lo <= 0xDFFF:
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                    i += 6
            out += chr(cp).encode("utf-8", "surrogatepass")
            continue
        raise ValueError("unknown escape in a string span")  # unreachable
    return bytes(out)


class Tape:
    """One parsed JSON tape. Owns the engine's record columns; the
    spans borrow ``source`` — the bytes this Tape keeps a reference to,
    so the borrow cannot dangle while the Tape lives. One tape per
    thread (the document law); ``close()`` frees everything in one
    call — afterwards the tape is dead and every accessor raises.
    """

    def __init__(self, source: bytes, raw: F._JsonTape):
        self._source = source
        self._raw = raw
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise YeptrisError("the tape is closed")

    def close(self) -> None:
        """Free the engine tape (idempotent)."""
        if self._closed:
            return
        self._closed = True
        F._lib.yeptris_tape_free(ctypes.byref(self._raw))

    def __enter__(self) -> "Tape":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __del__(self):  # pragma: no cover — GC finalization
        try:
            self.close()
        except Exception:
            pass

    def __len__(self) -> int:
        return self.count

    @property
    def count(self) -> int:
        """The record count (record 0 is the DOC boundary)."""
        self._require_open()
        return self._raw.count

    @property
    def source(self) -> bytes:
        """The parsed buffer; the spans are views into it."""
        return self._source

    def _materialize_columns(self) -> None:
        # no-op on the strict route (column-built) and on repeats — the
        # C call is idempotent and the lenient route needs it once
        if F._lib.yeptris_tape_columns(ctypes.byref(self._raw)) != 0:
            raise YeptrisError("tape column materialization failed")

    def kind_at(self, index: int) -> int:
        """Record ``index``'s kind byte (one of the F.TAPE_* values)."""
        self._require_open()
        if not 0 <= index < self._raw.count:
            raise IndexError(index)
        self._materialize_columns()
        return self._raw.kinds[index]

    def span(self, index: int) -> bytes:
        """Record ``index``'s raw source span (STR/INT/FLOAT/NUM; the
        escapes stay untouched — the tape law). Container records carry
        links, not spans, and raise ValueError."""
        self._require_open()
        if not 0 <= index < self._raw.count:
            raise IndexError(index)
        self._materialize_columns()
        kind = self._raw.kinds[index]
        if kind not in (F.TAPE_STR, F.TAPE_INT, F.TAPE_FLOAT, F.TAPE_NUM):
            raise ValueError(
                "record %d is a %s record: it carries a link, not a span"
                % (index, _KIND_NAMES.get(kind, kind)))
        off = self._raw.offs[index]
        return self._source[off:off + self._raw.lens[index]]

    def kinds(self) -> bytes:
        """The kinds column, one byte per record (copied in one read)."""
        self._require_open()
        self._materialize_columns()
        if not self._raw.count:
            return b""
        return ctypes.string_at(self._raw.kinds, self._raw.count)

    def offs(self) -> array.array:
        """The off column (array('I')): span starts, or the matching
        CLOSE index on the container records."""
        self._require_open()
        self._materialize_columns()
        if not self._raw.count:
            return array.array("I")
        return array.array("I", ctypes.string_at(self._raw.offs, 4 * self._raw.count))

    def lens(self) -> array.array:
        """The len column (array('I')): span lengths (0 on containers)."""
        self._require_open()
        self._materialize_columns()
        if not self._raw.count:
            return array.array("I")
        return array.array("I", ctypes.string_at(self._raw.lens, 4 * self._raw.count))

    def convert(self, from_record: int = 0,
                to_record: Union[int, None] = None) -> tuple:
        """The deferred-contract authority (``yeptris_tape_convert``):
        converts the number records in [from_record, to_record) —
        ivals/dvals are parallel to the RECORD indices (slots for other
        kinds stay zero). Returns (array('q'), array('d')). Raises
        ValueError when a span does not re-scan as a number (the
        lenient route's drain-side reject)."""
        self._require_open()
        to = self._raw.count if to_record is None else to_record
        if not 0 <= from_record <= to <= self._raw.count:
            raise ValueError("record range [%d, %d) is out of bounds"
                             % (from_record, to))
        iv = (ctypes.c_int64 * to)()
        dv = (ctypes.c_double * to)()
        n = F._lib.yeptris_tape_convert(ctypes.byref(self._raw), from_record,
                                        to, iv, dv)
        if n == _SIZE_MAX:
            raise ValueError(
                "a number span in [%d, %d) does not re-scan as a number "
                "(the lenient tape's deferred reject)" % (from_record, to))
        if not to:
            return array.array("q"), array.array("d")
        return array.array("q", iv), array.array("d", dv)

    def values(self) -> object:
        """Materialize the whole tape into Python values (dict / list /
        str / int / float / bool / None). Strings unescape; INT records
        rebuild exactly from their text (int64-overflow integers stay
        exact — the json.loads law); FLOAT records take the engine's
        converted doubles; the lenient NUM records classify at
        materialize time and reject malformed numbers with ValueError.
        """
        self._require_open()
        count = self._raw.count
        kinds = self.kinds()
        if count < 2:
            raise ValueError("the tape carries no value (empty document)")
        offs = self.offs()
        lens = self.lens()
        src = self._source
        _ivals, dvals = self.convert()

        def scalar(i: int):
            k = kinds[i]
            if k == F.TAPE_STR:
                return _unescape(src[offs[i]:offs[i] + lens[i]]).decode(
                    "utf-8", "surrogatepass")
            if k == F.TAPE_INT:
                # exact from the text: in-range the converted int64 is
                # the same number; beyond int64 the columnar slot does
                # not exist and the text is the truth (json.loads law)
                return int(src[offs[i]:offs[i] + lens[i]], 10)
            if k == F.TAPE_FLOAT:
                return dvals[i]
            if k == F.TAPE_NUM:  # lenient: the grammar was deferred
                text = src[offs[i]:offs[i] + lens[i]]
                if _INT_SHAPE.fullmatch(text):
                    return int(text, 10)
                if _FLOAT_SHAPE.fullmatch(text):
                    return dvals[i]
                raise ValueError(
                    "malformed number %r (the lenient tape's deferred "
                    "reject)" % text.decode("utf-8", "replace"))
            if k == F.TAPE_TRUE:
                return True
            if k == F.TAPE_FALSE:
                return False
            if k == F.TAPE_NULL:
                return None
            raise ValueError("unexpected record kind %d" % k)  # unreachable

        root = None
        placed = False
        stack = []   # the open containers, innermost last
        pending = []  # per level: the map's pending key (None = next is key)
        for i in range(count):
            k = kinds[i]
            if k == F.TAPE_DOC:
                continue
            if k == F.TAPE_SEQ_OPEN or k == F.TAPE_MAP_OPEN:
                v = [] if k == F.TAPE_SEQ_OPEN else {}
                _place(stack, pending, v, i)
                stack.append(v)
                pending.append(None)
                continue
            if k == F.TAPE_CLOSE:
                done = stack.pop()
                pending.pop()
                if not stack:
                    root, placed = done, True
                continue
            v = scalar(i)
            if _place(stack, pending, v, i):
                root, placed = v, True
        if not placed:
            raise ValueError("the tape did not materialize a value")
        return root


def _place(stack: list, pending: list, v, at: int) -> bool:
    """Place a completed value; True when it landed at top level."""
    if not stack:
        return True
    parent = stack[-1]
    if type(parent) is list:
        parent.append(v)
    else:
        key = pending[-1]
        if key is None:
            # the map's next slot is its key: parse guarantees keys are
            # STR records, so only a str may land here
            if not isinstance(v, str):
                raise ValueError("record %d: a mapping key is not a string"
                                 % at)
            pending[-1] = v
        else:
            pending[-1] = None
            parent[key] = v
    return False


def parse_tape(data: _Stream, *, lenient: bool = False) -> Tape:
    """Parse strict JSON into the tape (``yeptris_parse_json_tape``);
    ``lenient=True`` rides ``yeptris_parse_json_tape_lenient`` — every
    STRUCTURAL check stays at parse while number grammar defers to
    :meth:`Tape.convert` / :meth:`Tape.values`. Scalar roots are
    accepted (a single record). Raises ``yeptris.ParseError`` on
    anything the route rejects."""
    _require()
    b = _as_bytes(data)
    tape = F._JsonTape()
    fn = (F._lib.yeptris_parse_json_tape_lenient if lenient
          else F._lib.yeptris_parse_json_tape)
    st = fn(b, len(b), ctypes.byref(tape))
    if st != F.OK:
        msg, line, col = F.last_error()
        raise F._stage_error(st, msg, line, col)
    return Tape(b, tape)


_KIND_NAMES = {
    F.TAPE_DOC: "doc", F.TAPE_NULL: "null", F.TAPE_TRUE: "true",
    F.TAPE_FALSE: "false", F.TAPE_INT: "int", F.TAPE_FLOAT: "float",
    F.TAPE_STR: "str", F.TAPE_SEQ_OPEN: "seq-open", F.TAPE_MAP_OPEN: "map-open",
    F.TAPE_CLOSE: "close", F.TAPE_NUM: "num",
}
