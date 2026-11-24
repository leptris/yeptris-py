"""The event faces (events.h — TODO.impl/12): the pull model (a
StAX-style cursor; the run completes into the capture at construction,
events hand out one at a time or in bulk batches) and iterparse
(document-at-a-time over multi-document streams; one document's events
per slice, memory bounded by the largest document).

The engine frames every stream exactly once (``stream_start`` /
``stream_end``) and every document with a ``document_start`` /
``document_end`` pair — marker-less input frames too. iterparse's
first slice carries the synthesized ``stream_start`` ahead of document
one; the stream ends with a lone ``stream_end`` slice (a
zero-document stream yields one [``stream_start``, ``stream_end``]
slice and nothing else).

The C contract only guarantees event strings until the next call, so
the ``Event`` objects own COPIES of their bytes from the moment they
are handed out — a drained ``Event`` list outlives the puller freely.

These are the SAME event streams the recorder drain
(``_ffi.drain`` — load's route) rides: one parse, four consumption
models (the C-side cross-model corpus check is the proof).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from . import _ffi as F

__all__ = ["Event", "Iterparse", "Puller", "available", "iterparse",
           "pull", "OK",
           "STREAM_START", "STREAM_END", "DOCUMENT_START", "DOCUMENT_END",
           "SEQUENCE_START", "SEQUENCE_END", "MAPPING_START", "MAPPING_END",
           "SCALAR", "ALIAS"]

# YeptrisStatus / YeptrisEventType (events.h) — the ints behind kind()
OK = F.OK
STREAM_START = F.STREAM_START
STREAM_END = F.STREAM_END
DOCUMENT_START = F.DOCUMENT_START
DOCUMENT_END = F.DOCUMENT_END
SEQUENCE_START = F.SEQUENCE_START
SEQUENCE_END = F.SEQUENCE_END
MAPPING_START = F.MAPPING_START
MAPPING_END = F.MAPPING_END
SCALAR = F.SCALAR
ALIAS = F.ALIAS

_KIND_NAMES = {
    STREAM_START: "stream_start", STREAM_END: "stream_end",
    DOCUMENT_START: "document_start", DOCUMENT_END: "document_end",
    SEQUENCE_START: "sequence_start", SEQUENCE_END: "sequence_end",
    MAPPING_START: "mapping_start", MAPPING_END: "mapping_end",
    SCALAR: "scalar", ALIAS: "alias",
}

# YeptrisScalarStyle (dom.h): 0 = the non-scalars' unset style
_STYLE_NAMES = {F.STYLE_PLAIN: "plain",
                F.STYLE_SINGLE_QUOTED: "single_quoted",
                F.STYLE_DOUBLE_QUOTED: "double_quoted",
                F.STYLE_LITERAL: "literal",
                F.STYLE_FOLDED: "folded"}


def available() -> bool:
    """Whether the loaded libyeptris carries the pull/iterparse faces."""
    return bool(F.EVENTS_AVAILABLE)


def _require() -> None:
    if not F.EVENTS_AVAILABLE:
        raise F.YeptrisError(
            "the loaded libyeptris has no pull/iterparse faces; this "
            "surface requires a newer engine (set YEPTRIS_LIB_PATH to "
            "a newer build)")


def _span(ptr, length) -> str | None:
    """Copy a borrowed C string (valid until the next call) into a
    Python str — None when the pointer is NULL (the field's absent
    verdict)."""
    if not ptr:
        return None
    return ctypes.string_at(ptr, length).decode("utf-8")


@dataclass(frozen=True)
class Event:
    """One event, copied out of the engine's storage.

    ``type`` is the raw ``YeptrisEventType`` int (the module's
    ``STREAM_START``.. constants); ``kind`` its name ("scalar", ...).
    ``style`` is the scalar's presentation ("plain", "literal", ...) —
    None at the non-scalars. ``value`` is the scalar content or alias
    name (None when empty/absent); ``anchor``/``tag`` the node
    properties (None when absent; the tag is the RESOLVED tag).
    ``flow``/``explicit``/``implicit`` are the ``YEPTRIS_EF_*`` flag
    verdicts; the marks are 1-based, the ``end_*`` col one past the
    node's end token (the engine's #179 libyaml-mark law).
    """

    type: int
    kind: str
    style: str | None
    value: str | None
    anchor: str | None
    tag: str | None
    flow: bool
    explicit: bool
    implicit: bool
    line: int
    col: int
    end_line: int
    end_col: int

    @classmethod
    def _from_raw(cls, raw: F._Event) -> "Event":
        return cls(
            type=int(raw.type),
            kind=_KIND_NAMES.get(raw.type, "scalar"),
            style=_STYLE_NAMES.get(raw.style),
            value=_span(raw.value, raw.value_len),
            anchor=_span(raw.anchor, raw.anchor_len),
            tag=_span(raw.tag, raw.tag_len),
            flow=bool(raw.flow),
            explicit=bool(raw.explicit_marker),
            implicit=bool(raw.implicit),
            line=int(raw.line),
            col=int(raw.col),
            end_line=int(raw.end_line),
            end_col=int(raw.end_col),
        )


class Puller:
    """The pull cursor (``pull`` is the constructor). The whole parse
    completes at construction — a bad stream raises ParseError there,
    naming the position. ``next()`` hands out one ``Event`` (None at
    stream end); ``next_batch(n)`` up to n per call (the FFI shape:
    one crossing, never per event); iterating the puller drains it."""

    def __init__(self, source):
        raise TypeError("construct with pull(data)")

    @classmethod
    def _open(cls, source):
        _require()
        if isinstance(source, str):
            source = source.encode("utf-8")
        elif isinstance(source, (bytearray, memoryview)):
            source = bytes(source)
        puller = cls.__new__(cls)
        puller._buf = source
        puller._closed = False
        puller._raw = F._lib.yeptris_pull_new(source, len(source))
        if not puller._raw:
            raise F.YeptrisError("puller allocation failed")
        status, line, col = puller.status()
        if status != OK:
            puller.close()
            msg = F.last_error()[0]
            raise F._stage_error(status, msg or "parse error", line, col)
        return puller

    def _check_open(self) -> None:
        if self._closed:
            raise F.YeptrisError("the puller is closed")

    def next(self):
        """The next event, or None at stream end."""
        self._check_open()
        raw = F._lib.yeptris_pull_next(self._raw)
        if not raw:
            return None
        return Event._from_raw(raw[0])

    def next_batch(self, max: int) -> list:
        """Up to ``max`` events in ONE crossing ([] at stream end)."""
        self._check_open()
        if max <= 0:
            return []
        out = (F._Event * max)()
        n = int(F._lib.yeptris_pull_next_batch(self._raw, out, max))
        return [Event._from_raw(out[i]) for i in range(n)]

    def status(self):
        """``(status, line, col)`` of the puller's parse."""
        self._check_open()
        line = ctypes.c_uint32(0)
        col = ctypes.c_uint32(0)
        st = int(F._lib.yeptris_pull_status(self._raw, ctypes.byref(line),
                                            ctypes.byref(col)))
        return st, int(line.value), int(col.value)

    def close(self) -> None:
        if not self._closed:
            F._lib.yeptris_pull_free(self._raw)
            self._closed = True

    def __enter__(self) -> "Puller":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __iter__(self) -> "Puller":
        return self

    def __next__(self) -> Event:
        ev = self.next()
        if ev is None:
            raise StopIteration
        return ev

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class Iterparse:
    """Document-at-a-time iteration (``iterparse`` is the
    constructor): each ``next()`` yields ONE document's ``Event`` list
    (the first slice carries the synthesized ``stream_start``; the
    stream ends with a lone ``stream_end`` slice). A parse error
    raises ParseError at that slice."""

    def __init__(self, source):
        raise TypeError("construct with iterparse(data)")

    @classmethod
    def _open(cls, source):
        _require()
        if isinstance(source, str):
            source = source.encode("utf-8")
        elif isinstance(source, (bytearray, memoryview)):
            source = bytes(source)
        it = cls.__new__(cls)
        it._buf = source
        it._closed = False
        it._raw = F._lib.yeptris_iterparse_new(source, len(source))
        if not it._raw:
            raise F.YeptrisError("iterparse allocation failed")
        return it

    def _check_open(self) -> None:
        if self._closed:
            raise F.YeptrisError("the iterparse is closed")

    def status(self):
        """``(status, line, col)`` of the last slice's run."""
        self._check_open()
        line = ctypes.c_uint32(0)
        col = ctypes.c_uint32(0)
        st = int(F._lib.yeptris_iterparse_status(self._raw,
                                                 ctypes.byref(line),
                                                 ctypes.byref(col)))
        return st, int(line.value), int(col.value)

    def __iter__(self) -> "Iterparse":
        return self

    def __next__(self) -> list:
        self._check_open()
        count = ctypes.c_size_t(0)
        raw = F._lib.yeptris_iterparse_next(self._raw, ctypes.byref(count))
        if not raw:
            status, line, col = self.status()
            if status != OK:
                msg = F.last_error()[0]
                raise F._stage_error(status, msg or "parse error",
                                     line, col)
            raise StopIteration
        return [Event._from_raw(raw[i]) for i in range(count.value)]

    def close(self) -> None:
        if not self._closed:
            F._lib.yeptris_iterparse_free(self._raw)
            self._closed = True

    def __enter__(self) -> "Iterparse":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def pull(data) -> Puller:
    """Open the pull cursor over a YAML stream (see ``Puller``)."""
    return Puller._open(data)


def iterparse(data) -> Iterparse:
    """Iterate a YAML stream document by document (see ``Iterparse``)."""
    return Iterparse._open(data)
