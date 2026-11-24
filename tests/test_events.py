"""The event faces (yeptris/events.h): the pull model (StAX-style
cursor, bulk batch drain) and iterparse (document-at-a-time over
multi-document streams).

Pinned law (the header's contract):
- one parse feeds all four consumption models — identical event
  streams across them (test_events_cross is the C-side proof).
- STREAM_START / STREAM_END frame the stream exactly ONCE each; the
  DOCUMENT_* pair frames every document; marker-less input frames
  too (the engine's synthesized framing).
- pull: the run completes into the capture at construction; events
  hand out one at a time or in bulk batches; a parse error surfaces
  through the status accessor.
- iterparse: one document's events per slice (memory bounded by the
  largest document); the first slice carries the synthesized
  STREAM_START ahead of document one; the stream ends with a lone
  STREAM_END slice (a zero-document stream yields one
  [STREAM_START, STREAM_END] slice and nothing else).
- event strings cross the boundary COPIED: the C contract only
  guarantees them until the next call, so Python objects own their
  bytes from the moment they are handed out.
"""

import pytest

from yeptris import ParseError
from yeptris import _ffi as F
from yeptris import events as events_mod
from yeptris.events import Event, iterparse, pull

K = events_mod

pytestmark = pytest.mark.skipif(
    not F.EVENTS_AVAILABLE,
    reason="the loaded libyeptris predates the pull/iterparse faces (events.h)")


def kinds(evs):
    return [e.kind for e in evs]


def test_pull_frames_a_scalar_document():
    with pull("foo\n") as puller:
        evs = list(puller)
        assert kinds(evs) == [
            "stream_start", "document_start", "scalar", "document_end",
            "stream_end"]
        assert puller.status()[0] == K.OK


def test_scalar_event_fields():
    with pull("foo\n") as puller:
        ev = puller.next()
        while ev is not None and ev.kind != "scalar":
            ev = puller.next()
        assert isinstance(ev, Event)
        assert ev.value == "foo"
        assert ev.style == "plain"
        assert ev.implicit is True
        assert ev.anchor is None
        assert ev.line == 1 and ev.col == 1
        assert ev.end_line == 1 and ev.end_col == 4


def test_multi_document_stream():
    text = "a: 1\n---\nb: 2\n"
    with pull(text) as puller:
        evs = list(puller)
    ks = kinds(evs)
    # one stream pair, two document pairs
    assert ks.count("stream_start") == 1
    assert ks.count("stream_end") == 1
    assert ks.count("document_start") == 2
    assert ks.count("document_end") == 2
    # the document ends sit ahead of the lone stream end
    assert ks[-1] == "stream_end"
    assert ks.index("document_end", ks.index("document_end") + 1) < len(ks) - 1


def test_mapping_events():
    with pull("a: 1\n") as puller:
        evs = list(puller)
    assert kinds(evs) == [
        "stream_start", "document_start", "mapping_start", "scalar",
        "scalar", "mapping_end", "document_end", "stream_end"]
    # the key and the value are both scalars; the key carries is no
    # marker of its own — plain untagged both
    assert evs[3].value == "a" and evs[4].value == "1"


def test_flow_collection_flag():
    with pull("[1, 2]\n") as flow_puller:
        seq = flow_puller.next_batch(3)[2]
        assert seq.kind == "sequence_start"
        assert seq.flow is True
    with pull("q:\n  - 1\n") as block_puller:
        seq = [e for e in block_puller if e.kind == "sequence_start"][0]
        assert seq.flow is False


def test_explicit_marker_flag():
    with pull("--- 1\n...\n") as puller:
        evs = list(puller)
    doc_start = evs[1]
    assert doc_start.kind == "document_start"
    assert doc_start.explicit is True


def test_anchor_and_tag():
    with pull("a: &x !!str foo\n") as puller:
        evs = list(puller)
    anchored = [e for e in evs if e.kind == "scalar" and e.value == "foo"]
    assert len(anchored) == 1
    assert anchored[0].anchor == "x"
    # the engine event carries the RESOLVED tag (the header's
    # "resolved tag" wording)
    assert anchored[0].tag == "tag:yaml.org,2002:str"


def test_alias_event():
    with pull("a: &x 1\nb: *x\n") as puller:
        evs = list(puller)
    aliases = [e for e in evs if e.kind == "alias"]
    assert len(aliases) == 1
    assert aliases[0].value == "x"


def test_literal_style():
    with pull("a: |\n  x\n") as puller:
        evs = list(puller)
    scalars = [e for e in evs if e.kind == "scalar" and e.value == "x\n"]
    assert len(scalars) == 1
    assert scalars[0].style == "literal"


def test_unicode_value():
    with pull("é中文\n") as puller:
        evs = list(puller)
    scalars = [e for e in evs if e.kind == "scalar"]
    assert scalars[0].value == "é中文"


def test_next_batch_matches_sequential():
    text = "a: 1\n---\nb: 2\n---\n- 3\n"
    with pull(text) as p1:
        sequential = list(p1)
    with pull(text) as p2:
        batched = []
        while True:
            batch = p2.next_batch(3)
            if not batch:
                break
            batched.extend(batch)
    assert kinds(batched) == kinds(sequential)
    assert len(batched) == len(sequential)


def test_next_batch_smaller_than_stream():
    with pull("a: 1\n") as puller:
        first = puller.next_batch(2)
        assert kinds(first) == ["stream_start", "document_start"]
        rest = puller.next_batch(100)
        assert rest[-1].kind == "stream_end"
        assert puller.next_batch(10) == []


def test_pull_error_raises_at_construction():
    with pytest.raises(ParseError):
        pull("bad: [\n")


def test_iterparse_one_document_per_slice():
    text = "a: 1\n---\nb: 2\n"
    slices = list(iterparse(text))
    assert len(slices) == 3
    # slice one: STREAM_START + document one; slice two: document two;
    # final slice: the lone STREAM_END
    assert kinds(slices[0])[0] == "stream_start"
    assert kinds(slices[0]).count("document_end") == 1
    assert "stream_end" not in kinds(slices[0])
    assert kinds(slices[1]) == [
        "document_start", "mapping_start", "scalar", "scalar",
        "mapping_end", "document_end"]
    assert kinds(slices[2]) == ["stream_end"]
    flat = [e for s in slices for e in s]
    with pull(text) as puller:
        assert kinds(flat) == kinds(list(puller))


def test_iterparse_empty_stream():
    slices = list(iterparse(""))
    assert len(slices) == 1
    assert kinds(slices[0]) == ["stream_start", "stream_end"]


def test_iterparse_markerless_frames_too():
    slices = list(iterparse("foo\n"))
    assert len(slices) == 2
    assert kinds(slices[0]) == [
        "stream_start", "document_start", "scalar", "document_end"]
    assert kinds(slices[1]) == ["stream_end"]


def test_iterparse_error_raises():
    with pytest.raises(ParseError):
        list(iterparse("bad: [\n"))
