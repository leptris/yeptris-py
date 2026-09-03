"""Dumper: PyYAML-safe_dump parity, and dump->load round-trips."""

import datetime as dt

import pytest
import yaml as pyyaml

from yeptris import yaml as pyml


def test_block_style_sorted_keys():
    text = pyml.safe_dump({"b": 2, "a": 1})
    assert text == "a: 1\nb: 2\n"
    assert pyyaml.safe_load(text) == {"a": 1, "b": 2}


def test_sequences_and_none():
    text = pyml.safe_dump([1, "x", None, True, 3.5])
    assert text == "- 1\n- x\n- null\n- true\n- 3.5\n"


def test_resolvable_strings_get_quoted():
    text = pyml.safe_dump({"a": "yes", "b": "42", "c": "1.5", "d": "~",
                           "e": "2020-01-02", "f": "0x10"})
    doc = pyml.safe_load(text)
    assert doc == {"a": "yes", "b": "42", "c": "1.5", "d": "~",
                   "e": "2020-01-02", "f": "0x10"}


def test_indicator_strings_get_quoted():
    doc = pyml.safe_load(pyml.safe_dump({
        "a": "# comment-ish", "b": "k: v", "c": "- lead", "d": "tra ",
        "e": "with: colon", "f": "a #b", "g": "@at", "h": "|pipe",
    }))
    assert doc["a"] == "# comment-ish"
    assert doc["c"] == "- lead"
    assert doc["d"] == "tra "
    assert doc["f"] == "a #b"
    assert doc["g"] == "@at"


def test_merge_key_string_quoted():
    # a literal "<<" key must not become a merge on reparse: the
    # dumper quotes it, so the load comes back as a string key
    doc = pyml.safe_load(pyml.safe_dump({"<<": 1}))
    assert doc == {"<<": 1}


def test_datetimes():
    text = pyml.safe_dump({"t": dt.datetime(2020, 1, 2, 3, 4, 5),
                           "d": dt.date(2020, 1, 2)})
    doc = pyml.safe_load(text)
    assert doc == {"t": dt.datetime(2020, 1, 2, 3, 4, 5),
                   "d": dt.date(2020, 1, 2)}


def test_floats():
    doc = pyml.safe_load(pyml.safe_dump(
        [0.1, -2.5e300, float("inf"), float("-inf")]))
    assert doc[0] == 0.1
    assert doc[1] == -2.5e300
    assert doc[2] == float("inf")
    assert doc[3] == float("-inf")


def test_unicode():
    doc = pyml.safe_load(pyml.safe_dump({"name": "héllo wörld"}))
    assert doc == {"name": "héllo wörld"}


def test_unsafe_type_rejected():
    with pytest.raises(TypeError):
        pyml.safe_dump(object())


def test_deep_nesting_rejected_not_crashed():
    with pytest.raises(Exception):
        v = []
        cur = v
        for _ in range(2000):
            nxt = []
            cur.append(nxt)
            cur = nxt
        pyml.safe_dump(v)


def test_stream_write():
    import io
    out = io.StringIO()
    assert pyml.safe_dump({"a": 1}, out) is None
    assert out.getvalue() == "a: 1\n"


def test_roundtrip_against_pyyaml_random_documents():
    # a pile of shapes; whatever we dump must load identically under
    # BOTH loaders (we cannot emit YAML only we can read)
    docs = [
        {"lists": [[1], [2, [3]]], "maps": {"x": {"y": "z"}}},
        {"empty_list": [], "empty_map": {}, "empty_str": ""},
        ["nested", ["deeper", ["still"]]],
        {"bools": [True, False], "none": None},
        {"text": "line one\nline two\n"},
    ]
    for doc in docs:
        text = pyml.safe_dump(doc)
        assert pyml.safe_load(text) == doc
        assert pyyaml.safe_load(text) == doc
