"""The pull / iterparse event faces (events.h, C 0.6.77): the StAX
cursor and the document-at-a-time walk, driven through the released
engine's framing laws.

Pinned law (events.h + the rs binding's `event` face, mirrored here):
- STREAM_START / STREAM_END frame the stream exactly once each; every
  document (marker-less input frames too) rides a DOCUMENT_START /
  DOCUMENT_END pair.
- a bad document surfaces at ITERATION time — construction only
  allocates the puller (the engine parses into its store there; the
  only construction failure is allocation).
- the C strings live in the puller's arena and are valid UNTIL THE
  NEXT CALL; the Python Event owns its copies, so an Event survives
  the puller's close() and the stream's exhaustion.
"""

import pytest

from yeptris import _ffi as F
from yeptris import events
from yeptris._errors import ParseError, YeptrisError
from yeptris.events import EventKind, iterparse

pytestmark = pytest.mark.skipif(
    not F.EVENTS_AVAILABLE,
    reason="the loaded libyeptris predates the pull/iterparse faces "
           "(C 0.6.77)")


def kinds_of(events_iter):
    return [e.kind for e in events_iter]


# --- the framing law -------------------------------------------------------

def test_stream_frames_exactly_once():
    p = events.Puller("--- 1\n--- two\n--- [3]\n")
    ks = kinds_of(p)
    assert ks[0] == EventKind.STREAM_START
    assert ks[-1] == EventKind.STREAM_END
    assert ks.count(EventKind.STREAM_START) == 1
    assert ks.count(EventKind.STREAM_END) == 1
    assert ks.count(EventKind.DOCUMENT_START) == 3
    assert ks.count(EventKind.DOCUMENT_END) == 3


def test_markerless_input_still_frames_documents():
    # a single implicit document: the engine frames it anyway
    ks = kinds_of(events.Puller("foo\n"))
    assert ks == [EventKind.STREAM_START, EventKind.DOCUMENT_START,
                  EventKind.SCALAR, EventKind.DOCUMENT_END,
                  EventKind.STREAM_END]


def test_empty_stream_frames_the_stream_alone():
    assert kinds_of(events.Puller("")) == [EventKind.STREAM_START,
                                           EventKind.STREAM_END]


def test_empty_document_scalar_reads_null():
    evs = list(events.Puller("---\n"))
    scalars = [e for e in evs if e.kind == EventKind.SCALAR]
    assert len(scalars) == 1
    assert scalars[0].value is None  # the empty scalar reads as NULL
    assert scalars[0].value_str() is None


# --- the event fields ------------------------------------------------------

def test_scalar_fields_and_marks():
    evs = list(events.Puller("a: &x 1\nb: *x\n"))
    scalars = [e for e in evs if e.kind == EventKind.SCALAR]
    a_key, one, b_key = scalars
    assert a_key.value == b"a" and a_key.value_str() == "a"
    assert a_key.implicit and a_key.style == F.STYLE_PLAIN
    assert a_key.line == 1 and a_key.col == 1
    assert a_key.end_line == 1 and a_key.end_col == 2  # one past the token
    assert one.value == b"1"
    assert one.anchor == b"x"      # the anchor rides the value event
    assert one.tag is None         # plain scalars carry no explicit tag
    alias = [e for e in evs if e.kind == EventKind.ALIAS][0]
    assert alias.value == b"x"
    assert alias.implicit is False


def test_explicit_tag_resolves_to_the_uri():
    evs = list(events.Puller("a: !!str 7\n"))
    tagged = [e for e in evs if e.kind == EventKind.SCALAR
              and e.value == b"7"][0]
    assert tagged.tag == b"tag:yaml.org,2002:str"


def test_literal_style_and_value():
    evs = list(events.Puller("a: |\n  x\n"))
    lit = [e for e in evs if e.kind == EventKind.SCALAR and e.value == b"x\n"][0]
    assert lit.style == F.STYLE_LITERAL


def test_flow_and_marker_flags():
    evs = list(events.Puller("--- {a: [1]}\n"))
    by_kind = {e.kind: e for e in evs}
    assert by_kind[EventKind.DOCUMENT_START].explicit_marker is True
    assert by_kind[EventKind.MAPPING_START].flow is True
    assert by_kind[EventKind.SEQUENCE_START].flow is True


def test_eventkind_mirrors_the_abi_pinned_enum():
    assert [k.value for k in EventKind] == list(range(1, 11))


# --- the ownership law (the copied Event outlives the window) --------------

def test_events_survive_the_pullers_close():
    p = events.Puller("key: value\n")
    copied = list(p)
    p.close()
    # the C window binds only the internal call: the owned copies are
    # still readable after the puller is freed
    scalars = [e for e in copied if e.kind == EventKind.SCALAR]
    assert [e.value for e in scalars] == [b"key", b"value"]


# --- the batch face --------------------------------------------------------

def test_next_batch_chunks_and_totals():
    p = events.Puller("--- 1\n--- 2\n--- 3\n")
    total = 3 * 3 + 2  # SCALAR + DOC pair per document, stream frame once
    first = p.next_batch(3)
    assert [e.kind for e in first] == [EventKind.STREAM_START,
                                       EventKind.DOCUMENT_START,
                                       EventKind.SCALAR]
    second = p.next_batch(3)
    assert len(second) == 3
    rest = p.next_batch(100)
    assert sum(map(len, (first, second, rest))) == total


def test_next_batch_exhaustion_then_status_ok():
    p = events.Puller("a: 1\n")
    total = len(p.next_batch(4096))
    assert total == 8  # STREAM/DOC + MAP pair + 2 scalars + DOC/STREAM
    assert p.next_batch(4096) == []
    st, line, col = p.status()
    assert st == F.OK


def test_next_batch_guards():
    p = events.Puller("a: 1\n")
    with pytest.raises(ValueError):
        p.next_batch(0)
    p.close()
    with pytest.raises(YeptrisError):
        p.next_batch(10)


def test_next_batch_events_are_owned():
    p = events.Puller("a: 1\n")
    batch = p.next_batch(4096)
    p.close()
    scalars = [e for e in batch if e.kind == EventKind.SCALAR]
    assert [e.value_str() for e in scalars] == ["a", "1"]


# --- the error law ---------------------------------------------------------

def test_bad_document_raises_at_iteration():
    p = events.Puller("key: [1,\n")
    with pytest.raises(ParseError):
        list(p)


def test_failed_cursor_stays_failed():
    p = events.Puller("key: [1,\n")
    with pytest.raises(ParseError):
        list(p)
    st, line, col = p.status()
    assert st != F.OK
    assert (line, col) != (0, 0)
    with pytest.raises(ParseError):  # a fresh iteration re-raises
        list(p)


def test_input_forms_and_rejections():
    assert len(list(events.Puller(b"a: 1\n"))) == 8
    assert len(list(events.Puller("a: 1\n"))) == 8
    import io
    assert len(list(events.Puller(io.BytesIO(b"a: 1\n")))) == 8
    with pytest.raises(TypeError):
        events.Puller(42)


def test_context_manager_closes():
    with events.Puller("a: 1\n") as p:
        assert len(list(p)) == 8
    with pytest.raises(YeptrisError):
        p.status()


def test_close_is_idempotent():
    p = events.Puller("a: 1\n")
    p.close()
    p.close()


def test_garbage_collected_pullers_close_the_engine_handle():
    import gc
    import weakref
    p = events.Puller("a: 1\n")
    ref = weakref.ref(p)
    del p  # no explicit close: the __del__ finalizer owns the handle
    gc.collect()
    assert ref() is None


# --- iterparse -------------------------------------------------------------

def test_iterparse_slices_documents():
    slices = list(iterparse("--- 1\n--- [2]\n"))
    assert [[e.kind for e in sl] for sl in slices] == [
        [EventKind.STREAM_START, EventKind.DOCUMENT_START,
         EventKind.SCALAR, EventKind.DOCUMENT_END],
        [EventKind.DOCUMENT_START, EventKind.SEQUENCE_START,
         EventKind.SCALAR, EventKind.SEQUENCE_END, EventKind.DOCUMENT_END],
        [EventKind.STREAM_END],
    ]


def test_iterparse_single_document_yields_two_slices():
    slices = list(iterparse("---\n"))
    assert [[e.kind for e in sl] for sl in slices] == [
        [EventKind.STREAM_START, EventKind.DOCUMENT_START,
         EventKind.SCALAR, EventKind.DOCUMENT_END],
        [EventKind.STREAM_END],
    ]


def test_iterparse_empty_stream_yields_the_frame():
    slices = list(iterparse(""))
    assert len(slices) == 1
    assert [e.kind for e in slices[0]] == [EventKind.STREAM_START,
                                           EventKind.STREAM_END]


def test_iterparse_values():
    slices = list(iterparse("a: 1\n---\n- 2\n"))
    scalars = [e.value_str() for sl in slices for e in sl
               if e.kind == EventKind.SCALAR]
    assert scalars == ["a", "1", "2"]


def test_iterparse_raises_at_the_failing_document():
    with pytest.raises(ParseError):
        list(iterparse("---\na: 1\n---\nb: [\n"))


def test_iterparse_events_are_owned_after_the_walk():
    slices = list(iterparse("a: 1\n"))
    scalars = [e for sl in slices for e in sl if e.kind == EventKind.SCALAR]
    assert [e.value for e in scalars] == [b"a", b"1"]
