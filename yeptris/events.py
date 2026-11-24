"""The pull / iterparse event faces (events.h): the two host-driven
consumption models over the one parse engine.

- ``Puller`` — the StAX-style cursor (the yeptris-rs ``event`` face's
  shape): the whole parse runs into the puller's event store at
  construction; iteration hands out one stored event at a time.
  ``next_batch`` is the FFI-shaped bulk form (``yeptris_pull_next_batch``):
  up to N events per crossing, the O(chunks) law instead of O(events).
- ``iterparse`` — document-at-a-time over multi-document streams: one
  document's events per yield, memory bounded by the largest document.
  STREAM_START / STREAM_END frame the stream exactly once each; the
  DOCUMENT_* pair frames each yielded slice.

Every event is an owned ``Event`` — the C strings live in the model's
arena and are valid only until the next call, so the wrappers copy at
hand-out and the C validity window never escapes into Python (an
``Event`` stays valid forever, unlike the raw C contract).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, Union

from . import _ffi as F
from ._errors import YeptrisError
from ._loader import _as_bytes

__all__ = ["Event", "EventKind", "Puller", "available", "iterparse"]

_Stream = Union[str, bytes]


def available() -> bool:
    """Whether the loaded libyeptris carries the event faces
    (libyeptris >= 0.6.77)."""
    return bool(F.EVENTS_AVAILABLE)


def _require() -> None:
    if not F.EVENTS_AVAILABLE:
        raise YeptrisError(
            "the loaded libyeptris has no pull/iterparse event faces; this "
            "surface requires libyeptris >= 0.6.77 (set YEPTRIS_LIB_PATH to "
            "a newer build)")


class EventKind(IntEnum):
    """The event's kind — mirrors ``YeptrisEventType`` (events.h); the
    values are the ABI-pinned C enum."""

    STREAM_START = 1
    STREAM_END = 2
    DOCUMENT_START = 3
    DOCUMENT_END = 4
    SEQUENCE_START = 5
    SEQUENCE_END = 6
    MAPPING_START = 7
    MAPPING_END = 8
    SCALAR = 9
    ALIAS = 10


@dataclass(frozen=True)
class Event:
    """One parse event, owned (copied out of the puller's window).

    ``value`` is the scalar content or alias name as raw bytes — None
    when the engine recorded no value (the empty scalar reads as NULL;
    the event face mirrors the C contract). ``anchor``/``tag`` are the
    node's properties when present, else None. ``line``/``col`` are the
    1-based start mark; ``end_line``/``end_col`` the libyaml end-mark
    semantics (#179) — the end col is one past the node's end token.
    ``style`` is a ``YeptrisScalarStyle`` int (F.STYLE_*) for scalars;
    ``flow``/``explicit_marker``/``implicit`` carry the C flags.
    """

    kind: EventKind
    style: int
    value: Union[bytes, None]
    anchor: Union[bytes, None]
    tag: Union[bytes, None]
    line: int
    col: int
    end_line: int
    end_col: int
    flow: bool
    explicit_marker: bool
    implicit: bool

    def value_str(self) -> Union[str, None]:
        """The value as UTF-8 text (None when the event carries none)."""
        if self.value is None:
            return None
        return self.value.decode("utf-8")

    @staticmethod
    def _take(raw: F._Event) -> "Event":
        """Copy one C event view into an owned Event. Must run within
        the C validity window (before the next pull call)."""
        return Event(
            kind=EventKind(raw.type),
            style=raw.style,
            value=F.copy_span(raw.value, raw.value_len),
            anchor=F.copy_span(raw.anchor, raw.anchor_len),
            tag=F.copy_span(raw.tag, raw.tag_len),
            line=raw.line,
            col=raw.col,
            end_line=raw.end_line,
            end_col=raw.end_col,
            flow=bool(raw.flow),
            explicit_marker=bool(raw.explicit_marker),
            implicit=bool(raw.implicit),
        )


class Puller:
    """A pull cursor over one input. Owns the engine's puller; the
    whole parse runs into its event store at construction (the only
    failure is the host-side allocation), so a bad document surfaces at
    ITERATION time, not here. One document per thread (the engine law);
    ``close()`` frees it — after that the cursor is dead.
    """

    def __init__(self, data: _Stream):
        _require()
        b = _as_bytes(data)
        self._pull = F._lib.yeptris_pull_new(b, len(b))
        if not self._pull:
            raise YeptrisError("pull allocation failed")

    def _require_open(self) -> None:
        if not self._pull:
            raise YeptrisError("the puller is closed")

    def close(self) -> None:
        """Free the engine puller (idempotent)."""
        pull, self._pull = self._pull, None
        if pull:
            F._lib.yeptris_pull_free(pull)

    def __del__(self):  # the GC finalizer over the same liveness flag
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "Puller":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def status(self) -> tuple:
        """The cursor's verdict: (status, line, col) — OK once the
        stream is exhausted; the engine error after a failed parse."""
        self._require_open()
        line = ctypes.c_uint32(0)
        col = ctypes.c_uint32(0)
        st = F._lib.yeptris_pull_status(self._pull, ctypes.byref(line),
                                        ctypes.byref(col))
        return st, line.value, col.value

    def next_batch(self, max_events: int = 4096) -> list:
        """The bulk cursor (``yeptris_pull_next_batch``): up to
        ``max_events`` events per FFI crossing — O(chunks), never
        O(events). An empty list means the stream is exhausted OR the
        parse failed; ``status()`` distinguishes. The returned Events
        are owned copies (the C window binds only the internal call).
        """
        self._require_open()
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        buf = (F._Event * max_events)()
        n = F._lib.yeptris_pull_next_batch(self._pull, buf, max_events)
        return [Event._take(buf[i]) for i in range(n)]

    def __iter__(self) -> Iterator[Event]:
        """The StAX cursor: yields every event of the stream. A clean
        end returns; a parse failure raises (the engine's message with
        its 1-based position folded in) — the same law as the loaders:
        bad input raises at iteration time. Rides ``next_batch``
        internally, so the FFI tax stays O(chunks)."""
        while True:
            batch = self.next_batch()
            if batch:
                yield from batch
                continue
            st, line, col = self.status()
            if st == F.OK:
                return
            msg, _, _ = F.last_error()
            raise F._stage_error(st, msg, line, col)


def iterparse(data: _Stream) -> Iterator[list]:
    """Document-at-a-time over a multi-document stream: one yield per
    document, carrying that document's event slice (a list of Events,
    DOCUMENT_START..DOCUMENT_END; the stream's single STREAM_START /
    STREAM_END slices come first and last). Memory is bounded by the
    largest document, and a mid-stream failure raises with that
    document's own position.
    """
    _require()
    b = _as_bytes(data)
    it = F._lib.yeptris_iterparse_new(b, len(b))
    if not it:
        raise YeptrisError("iterparse allocation failed")
    count = F._sz(0)
    try:
        while True:
            ev = F._lib.yeptris_iterparse_next(it, ctypes.byref(count))
            if not ev:
                line = ctypes.c_uint32(0)
                col = ctypes.c_uint32(0)
                st = F._lib.yeptris_iterparse_status(it, ctypes.byref(line),
                                                     ctypes.byref(col))
                if st != F.OK:
                    msg, _, _ = F.last_error()
                    raise F._stage_error(st, msg, line.value, col.value)
                return
            yield [Event._take(ev[i]) for i in range(count.value)]
    finally:
        F._lib.yeptris_iterparse_free(it)
