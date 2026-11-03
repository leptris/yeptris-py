"""The tolerant per-document stream face (#921 / py#168).

Pins: the mixed stream (valid/INVALID/valid with per-document
line/col + index), the recovery law (the failed frame resyncs to the
next line-start marker; the raw scan is the honest best effort), the
quoted-marker one-unit framing, the unrecoverable tail terminating,
the empty/comment streams, the schema option per frame, and
depth-failure recovery with a bound. Skips on engines predating the
slice accessor (the capability-probe law)."""

import pytest

import yeptris
from yeptris import _ffi as F
from yeptris import yaml

pytestmark = pytest.mark.skipif(
    not (F.STREAM_ITER_AVAILABLE and F.STREAM_FRAMES_AVAILABLE),
    reason="the loaded libyeptris predates the stream iterator's "
           "slice accessor (#921)")


def test_each_document_yields_every_valid_document():
    pairs = list(yaml.each_document("--- 41\n--- \"42\"\n---\n"))
    assert pairs == [(41, None), ("42", None), (None, None)]


def test_each_document_is_lazy():
    gen = yaml.each_document("---\na: 1\n---\nb: 2\n")
    assert not isinstance(gen, list)  # a generator: nothing parsed yet
    pairs = list(gen)
    assert pairs == [({"a": 1}, None), ({"b": 2}, None)]


def test_each_document_keeps_valid_documents_around_a_bad_one():
    pairs = list(yaml.each_document("--- ok_one\n--- {{{{ bad\n--- ok_two\n"))
    assert pairs[0] == ("ok_one", None)
    doc, err = pairs[1]
    assert doc is None
    assert "stream document 1" in str(err)
    assert "line 2" in str(err)
    assert pairs[2] == ("ok_two", None)


def test_each_document_frames_scanner_accurately():
    # the grammar rejects the quoted scalar as ONE unit; the raw-marker
    # resync then lands on the --- INSIDE the quoted text (the failed
    # frame has no scanner-defined extent), so the tail parses
    # best-effort — the pin: one failure, the tail surfaces, no hang
    pairs = list(yaml.each_document("\"line one\n---\nline two\"\n"))
    assert pairs[0][0] is None
    assert isinstance(pairs[0][1], yeptris.ParseError)
    assert pairs[1] == ("line two\"", None)


def test_each_document_ends_on_an_unrecoverable_tail():
    deep = "[" * 1200 + "]" * 1200
    pairs = list(yaml.each_document("--- [1]\n%s\n" % deep))
    assert len(pairs) == 1
    assert pairs[0][0] is None
    assert isinstance(pairs[0][1], yeptris.ParseError)


def test_each_document_empty_and_comment_streams():
    assert list(yaml.each_document("")) == []
    assert list(yaml.each_document("# just a comment\n")) == []


def test_each_document_rides_the_schema_per_frame():
    # compat_11 (default, the PyYAML/libyaml surface) keeps 0o7 a
    # string; the 1.2 core resolver types it octal
    assert list(yaml.each_document("--- 0o7\n")) == [("0o7", None)]
    assert list(yaml.each_document("--- 0o7\n",
                                   schema=F.SCHEMA_12_CORE)) == [(7, None)]


def test_each_document_recovers_past_a_depth_failure():
    deep = "[" * 50 + "]" * 50
    pairs = list(yaml.each_document("--- %s\n--- recovered\n" % deep,
                                    max_depth=10))
    doc, err = pairs[0]
    assert doc is None
    assert "depth limit exceeded" in str(err)
    assert pairs[1] == ("recovered", None)
