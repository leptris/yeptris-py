"""Dumper: PyYAML-safe_dump parity, and dump->load round-trips."""

import datetime as dt

import pytest
import yaml as pyyaml

from yeptris import yaml as pyml
from yeptris.yaml import RepresenterError


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


# Issue #52 regression: load() must return plain string keys (no colon
# prefix), and dump(load(x)) must round-trip every plain mapping key —
# the historical defect returned {':config': ...} and dump raised
# ValueError ("could not convert string to float: 'config'").
def test_issue52_colon_keys_and_roundtrip():
    src = b"config:\n  database:\n    host: localhost\n"
    r = pyml.load(src)
    assert r == {"config": {"database": {"host": "localhost"}}}
    assert all(not str(k).startswith(":") for k in r)
    assert pyml.load(pyml.dump(r)) == r


def test_issue52_numeric_looking_keys_roundtrip():
    for src in (b"5014: x\n", b"1.5: y\n", b"\"5014\": quoted\n"):
        r = pyml.load(src)
        assert pyml.load(pyml.dump(r)) == r


def test_colon_bearing_strings_dump_without_crash():
    # issue #52's remaining face: any ':'-containing VALUE (or key)
    # reached _to_float's sexagesimal arm and raised ValueError out
    # of safe_dump — PyYAML's resolver regex gates the shape first
    doc = pyml.safe_load(pyml.safe_dump({
        "sym": ":a", "url": "https://example.com/x", "mixed": "12:34abc",
        "time": "12:34:56", "sexafloat": "1:30.5", "a:b": "v", ":k": 1,
    }))
    assert doc["sym"] == ":a"
    assert doc["url"] == "https://example.com/x"
    assert doc["mixed"] == "12:34abc"
    assert doc["time"] == "12:34:56"
    assert doc["sexafloat"] == "1:30.5"
    assert doc["a:b"] == "v"
    assert doc[":k"] == 1


def test_colon_bearing_strings_pyyaml_roundtrip():
    # the same documents through the reference implementation
    h = {"sym": ":a", "url": "https://example.com/x", "mixed": "12:34abc"}
    text = pyml.safe_dump(h)
    assert pyyaml.safe_load(text) == h


# --- PyYAML representer parity (audit round 7) ---------------------------

def test_set_dumps_as_pyyaml_set():
    # PyYAML's represent_set: a !!set-tagged mapping with null values.
    # The old list form read back as a LIST in every loader — a silent
    # data re-shape; the tagged mapping reads back as a set in PyYAML.
    text = pyml.safe_dump({1, 2})
    assert text == "!!set\n1: null\n2: null\n"
    assert pyyaml.safe_load(text) == {1, 2}
    # the loader side constructs the set now too (issue #166: the
    # engine's drains carry the collection's tag text) — the dump/load
    # round-trip holds in our own loader
    assert pyml.safe_load(text) == {1, 2}


def test_sets_inside_collections_roundtrip_through_pyyaml():
    assert pyyaml.safe_load(pyml.safe_dump({"s": {"b", "a"}})) == \
        {"s": {"a", "b"}}
    assert pyyaml.safe_load(pyml.safe_dump([{"x", 1}])) == [{1, "x"}]
    assert pyyaml.safe_load(pyml.safe_dump({True})) == {True}


def test_frozenset_is_unrepresentable_parity():
    # PyYAML registers no SafeRepresenter for frozenset: it raises.
    # Ours used to dump it as a list — the same silent re-shape.
    with pytest.raises(pyyaml.representer.RepresenterError):
        pyyaml.safe_dump(frozenset({1}))
    with pytest.raises(RepresenterError):
        pyml.safe_dump(frozenset({1}))


def test_ordereddict_is_unrepresentable_parity():
    # PyYAML's SafeDumper has no representer for dict subclasses: an
    # OrderedDict raises there (only the full yaml.Dumper maps it to
    # !!omap). Ours used to dump one as a PLAIN sorted map — silently
    # discarding the order the type exists to carry (issue #166, the
    # dump-side twin of the omap constructor: !!omap loads as a pairs
    # LIST, and safe_dump has no OrderedDict form to pair with it).
    import collections

    od = collections.OrderedDict([("b", 2), ("a", 1)])
    with pytest.raises(pyyaml.representer.RepresenterError):
        pyyaml.safe_dump(od)
    with pytest.raises(RepresenterError):
        pyml.safe_dump(od)


def test_dict_subclasses_are_unrepresentable_parity():
    # the exact-type law covers every dict subclass, like PyYAML
    import collections

    for value in (collections.Counter({"a": 1}), collections.defaultdict(int)):
        with pytest.raises(pyyaml.representer.RepresenterError):
            pyyaml.safe_dump(value)
        with pytest.raises(RepresenterError):
            pyml.safe_dump(value)


def test_datetime_time_is_unrepresentable_parity():
    # PyYAML raises on datetime.time (no representer); ours did too,
    # via the fall-through — pinned so a future representer change is
    # a decision, not a drift
    with pytest.raises(pyyaml.representer.RepresenterError):
        pyyaml.safe_dump(dt.time(3, 4, 5))
    with pytest.raises(RepresenterError):
        pyml.safe_dump(dt.time(3, 4, 5))


def test_str_int_subclasses_are_unrepresentable_parity():
    # PyYAML's representers key on exact type; subclasses raise there
    # and fall through to the same arm here
    class MyStr(str):
        pass

    class MyInt(int):
        pass

    for value in (MyStr("x"), MyInt(7)):
        with pytest.raises(pyyaml.representer.RepresenterError):
            pyyaml.safe_dump(value)
        with pytest.raises(RepresenterError):
            pyml.safe_dump(value)


# --- the dump kwargs (audit round 7: never a silent `_ignored`) ----------

def _fold_default():
    # the engine's #1257 flip folds block plain scalars at the default
    # 80 (PyYAML parity — the old "pinned divergence" is gone); engines
    # <= 0.6.61 unwrap. Probe the BEHAVIOR, not a version: the CI
    # engine pin moves at its own cadence.
    doc = {"text": "word " * 30}
    return pyml.safe_dump(doc).count("\n") > 1


FOLD_DEFAULT = _fold_default()


def test_width_is_accepted_and_block_width_follows_the_fold():
    doc = {"text": "word " * 40, "k": list(range(30))}
    base = pyml.safe_dump(doc)
    narrow = pyml.safe_dump(doc, width=20)
    assert pyml.load(narrow) == doc
    assert pyml.safe_dump(doc, width=80) == base
    if FOLD_DEFAULT:
        # the flip: width 20 folds the plain scalar harder than the
        # default 80
        assert narrow.count("\n") > base.count("\n")
    else:
        assert narrow == base


def test_width_rejects_non_positive():
    # the engine maps a non-positive best_width to its default 80 —
    # a silent surprise PyYAML's width=0-means-infinite callers must
    # not eat
    with pytest.raises(ValueError):
        pyml.safe_dump({"a": 1}, width=0)
    with pytest.raises(ValueError):
        pyml.safe_dump({"a": 1}, width=-5)


def test_explicit_start_single_document():
    # the dump_all separator law ('---\n'), not PyYAML's same-line
    # '--- 1' form — one marker style per binding
    text = pyml.safe_dump({"a": 1}, explicit_start=True)
    assert text == "---\na: 1\n"
    assert pyml.load(text) == {"a": 1}
    assert pyyaml.safe_load(text) == {"a": 1}


def test_dump_all_threads_width():
    doc = {"text": "word " * 40}
    base = pyml.safe_dump_all([doc, doc])
    narrow = pyml.safe_dump_all([doc, doc], width=20)
    if FOLD_DEFAULT:
        assert narrow.count("\n") > base.count("\n")
    else:
        assert narrow == base


def test_pyyaml_only_dump_kwargs_are_accepted():
    # indent / default_flow_style ride for call compatibility: the
    # dump face is block-only and the engine's emit options have no
    # indent field (carried engine gaps — accepted and documented,
    # never a silent `_ignored` swallow)
    assert pyml.safe_dump({"a": 1}, indent=4,
                          default_flow_style=True) == "a: 1\n"
    assert pyml.safe_dump_all([{"a": 1}], indent=9) == "a: 1\n"
