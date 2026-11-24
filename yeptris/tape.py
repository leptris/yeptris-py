"""The compact JSON tape (tape.h): ONE record per token in THREE
parallel columns — kind byte, off, len — carved from one block. No
DOM nodes, no links, no views: strings stay zero-copy spans into the
caller's buffer and NUMBERS RECORD SPANS ONLY (parse validates the
grammar; materialize converts — ``Tape.convert``, simdjson's deferred
contract).

Record kinds (``KIND_*``): the document boundary record (``KIND_DOC``)
opens every tape; bools encode in the kind (``KIND_TRUE``/``KIND_FALSE``
carry no span); ``KIND_INT``/``KIND_FLOAT``/``KIND_STR`` carry
(off,len) spans (``Tape.span`` / ``Tape.bytes`` — the bytes are RAW:
string escapes stay untouched); containers ride ``KIND_SEQ_OPEN`` /
``KIND_MAP_OPEN`` / ``KIND_CLOSE`` whose off column holds the matching
record's index (``Tape.link`` — mutual both ways).

Two parse routes: strict (default — RFC 8259's number grammar checked
at parse) and ``lenient=True`` (every STRUCTURAL check stays at parse
while number grammar defers to ``convert``: a ``1.2.3`` run parses and
rejects at convert; a run with non-number bytes still rejects at
parse; tab whitespace becomes legal, RFC 8259). On everything the
strict route accepts the tapes are structurally identical.

The spans borrow the parsed buffer, so the tape keeps its source
alive for its lifetime; ``close()`` (or the context manager) frees the
engine side. ``columns()`` materializes the compat columns (the
engine's ``yeptris_tape_columns``) and copies them out as array.array.
"""

from __future__ import annotations

import array
import ctypes

from . import _ffi as F

__all__ = ["Tape", "available", "parse_tape",
           "KIND_DOC", "KIND_NULL", "KIND_TRUE", "KIND_FALSE",
           "KIND_INT", "KIND_FLOAT", "KIND_STR",
           "KIND_SEQ_OPEN", "KIND_MAP_OPEN", "KIND_CLOSE", "KIND_NUM"]

# record kinds (tape.h)
KIND_DOC = 0
KIND_NULL = 1
KIND_TRUE = 2
KIND_FALSE = 3
KIND_INT = 4
KIND_FLOAT = 5
KIND_STR = 6
KIND_SEQ_OPEN = 7
KIND_MAP_OPEN = 8
KIND_CLOSE = 9
KIND_NUM = 10

_SPAN_KINDS = frozenset((KIND_INT, KIND_FLOAT, KIND_STR, KIND_NUM))
_CONTAINER_KINDS = frozenset((KIND_SEQ_OPEN, KIND_MAP_OPEN, KIND_CLOSE))

_SIZE_MAX = ctypes.c_size_t(-1).value


def available() -> bool:
    """Whether the loaded libyeptris carries the tape face."""
    return bool(F.TAPE_AVAILABLE)


def _require() -> None:
    if not F.TAPE_AVAILABLE:
        raise F.YeptrisError(
            "the loaded libyeptris has no tape face; this surface "
            "requires a newer engine (set YEPTRIS_LIB_PATH to a newer "
            "build)")


class Tape:
    """A parsed JSON tape (see the module docstring). Construct with
    ``parse_tape``."""

    def __init__(self, source, lenient: bool = False):
        raise TypeError("parse with parse_tape(data, lenient=False)")

    @classmethod
    def _parse(cls, source, lenient):
        _require()
        if isinstance(source, str):
            source = source.encode("utf-8")
        elif isinstance(source, (bytearray, memoryview)):
            source = bytes(source)
        tape = cls.__new__(cls)
        tape._t = F._JsonTape()
        tape._src = source
        tape._closed = False
        tape._cols = None
        st = (F._lib.yeptris_parse_json_tape_lenient if lenient
              else F._lib.yeptris_parse_json_tape)(source, len(source),
                                                   ctypes.byref(tape._t))
        if st != F.OK:
            msg, line, col = F.last_error()
            F._lib.yeptris_tape_free(ctypes.byref(tape._t))
            tape._closed = True
            raise F._stage_error(st, msg or "invalid JSON", line, col)
        return tape

    def _check_open(self) -> None:
        if self._closed:
            raise F.YeptrisError("the tape is closed")

    @property
    def count(self) -> int:
        """The record count (the DOC boundary included)."""
        self._check_open()
        return int(self._t.count)

    def _ensure_columns(self) -> None:
        if self._cols is not None:
            return
        if F._lib.yeptris_tape_columns(ctypes.byref(self._t)) != 0:
            raise F.YeptrisError("tape column materialization failed")
        n = int(self._t.count)
        self._cols = (
            array.array("B", ctypes.string_at(self._t.kinds, n)) if n
            else array.array("B"),
            array.array("I", ctypes.string_at(self._t.offs, 4 * n)) if n
            else array.array("I"),
            array.array("I", ctypes.string_at(self._t.lens, 4 * n)) if n
            else array.array("I"),
        )

    def columns(self):
        """The compat columns materialized and copied:
        ``(kinds, offs, lens)`` as array.array (B, I, I)."""
        self._check_open()
        self._ensure_columns()
        return (array.array("B", self._cols[0]),
                array.array("I", self._cols[1]),
                array.array("I", self._cols[2]))

    def kind(self, i: int) -> int:
        """Record ``i``'s kind (one of the ``KIND_*`` constants)."""
        self._check_open()
        self._ensure_columns()
        return int(self._cols[0][i])

    def span(self, i: int):
        """Record ``i``'s (off, len) span — None at the kinds that
        carry no span (DOC/NULL/TRUE/FALSE)."""
        self._check_open()
        self._ensure_columns()
        if self._cols[0][i] not in _SPAN_KINDS:
            return None
        return (int(self._cols[1][i]), int(self._cols[2][i]))

    def bytes(self, i: int):
        """Record ``i``'s raw span bytes (escapes untouched), copied —
        None at the kinds that carry no span."""
        self._check_open()
        self._ensure_columns()
        kind = self._cols[0][i]
        if kind not in _SPAN_KINDS:
            return None
        return self._span_bytes(int(self._cols[1][i]),
                                int(self._cols[2][i]))

    def _span_bytes(self, off: int, ln: int) -> bytes:
        """A raw span of the parsed buffer (the plan drain's string
        reader; the spans borrow the source, which the tape keeps
        alive)."""
        return ctypes.string_at(self._t._src + off, ln)

    def link(self, i: int):
        """The record matching ``i``'s container (OPEN <-> CLOSE,
        mutual); None at the kinds that carry no link."""
        self._check_open()
        self._ensure_columns()
        if self._cols[0][i] not in _CONTAINER_KINDS:
            return None
        return int(self._cols[1][i])

    def convert(self, from_: int = 0, to: int | None = None):
        """Convert the INT/FLOAT/NUM record spans in [from_, to) —
        returns ``(count, ivals, dvals)`` where ivals/dvals are
        PARALLEL TO THE RECORD INDICES (lists of length ``to``; slots
        for other kinds are untouched zeros). Raises ParseError when a
        span does not re-scan as a number (the lenient route's
        deferred verdict)."""
        self._check_open()
        self._ensure_columns()
        if to is None:
            to = int(self._t.count)
        if to < from_ or from_ < 0:
            raise ValueError("bad convert range")
        ivals = (ctypes.c_int64 * to)()
        dvals = (ctypes.c_double * to)()
        if to == from_:
            return 0, list(ivals), list(dvals)
        n = int(F._lib.yeptris_tape_convert(ctypes.byref(self._t), from_,
                                            to, ivals, dvals))
        if n == _SIZE_MAX:
            msg, line, col = F.last_error()
            raise F._stage_error(F.ERROR_PARSE,
                                 msg or "a number span failed to convert",
                                 line, col)
        return n, list(ivals), list(dvals)

    def close(self) -> None:
        if not self._closed:
            F._lib.yeptris_tape_free(ctypes.byref(self._t))
            self._closed = True
            self._cols = None

    def __enter__(self) -> "Tape":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def parse_tape(source, lenient: bool = False) -> Tape:
    """Parse strict JSON into the tape (``lenient=True``: the deferred
    number-grammar route). Accepts scalar roots (a single record).
    Raises ParseError on malformed input."""
    return Tape._parse(source, lenient)
