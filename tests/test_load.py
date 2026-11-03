"""Loader semantics: differential against PyYAML where the two must
agree, explicit pins where they diverge by design."""

import datetime as dt
import math

import pytest
import yaml as pyyaml

import yeptris
from yeptris import ConstructorError, ParseError
from yeptris import yaml as pyml


AGREE_CASES = [
    # (yaml, expected) — checked against BOTH yeptris and PyYAML
    ("k: v", {"k": "v"}),
    ("a: 1", {"a": 1}),
    ("a: -42", {"a": -42}),
    ("a: 1.5", {"a": 1.5}),
    ("a: 1e3", {"a": "1e3"}),              # PyYAML: needs a dot -> str
    ("a: 1.5e-3", {"a": 0.0015}),
    ("a: 0x1F", {"a": 31}),
    ("a: 0o17", {"a": "0o17"}),            # PyYAML 1.1 has no 0o form
    ("a: 010", {"a": 8}),                  # PyYAML 1.1 leading-0 octal
    ("a: 1_000", {"a": 1000}),
    ("a: 0b1010", {"a": 10}),
    ("a: yes", {"a": True}),
    ("a: no", {"a": False}),
    ("a: on", {"a": True}),
    ("a: off", {"a": False}),
    ("a: true", {"a": True}),
    ("a: null", {"a": None}),
    ("a: ~", {"a": None}),
    ("a:", {"a": None}),
    ("- 1\n- two\n- 3.5", [1, "two", 3.5]),
    ("[]", []),
    ("{}", {}),
    ("{a: 1, b: [2, 3]}", {"a": 1, "b": [2, 3]}),
    ("'42': quoted", {"42": "quoted"}),
    ('"42": quoted', {"42": "quoted"}),
    ("a: 'single'", {"a": "single"}),
    ('a: "double #not comment"', {"a": "double #not comment"}),
    ("a: |\n  line1\n  line2\n", {"a": "line1\nline2\n"}),
    ("a: >\n  fold\n  ed\n", {"a": "fold ed\n"}),
    ("a: .inf\nb: -.inf", {"a": math.inf, "b": -math.inf}),
    ("a: 190:20:30", {"a": 685230}),        # sexagesimal int
    ("a: 190:20:30.15", {"a": 685230.15}),  # sexagesimal float
    ("&a [*a]", None),                      # self-ref -> handled separately
]


@pytest.mark.parametrize("text,expected", AGREE_CASES[:-1])
def test_matches_pyyaml(text, expected):
    ours = pyml.safe_load(text)
    theirs = pyyaml.safe_load(text)
    assert ours == expected
    assert theirs == expected, f"PyYAML disagrees about the fixture itself: {theirs!r}"


def test_self_referencing_alias():
    ours = pyml.safe_load("&a [*a]")
    theirs = pyyaml.safe_load("&a [*a]")
    assert isinstance(ours, list)
    assert ours[0] is ours          # identity preserved, like PyYAML
    assert theirs[0] is theirs


DIVERGENT = [
    # (yaml, ours, pyyaml) — pinned, documented divergences
    ("a: y", {"a": "y"}, {"a": "y"}),       # same outcome, different reason
    ("a: 1:2:3", {"a": 3723}, {"a": 3723}), # both sexagesimal
    ("a: 0o17", {"a": "0o17"}, {"a": "0o17"}),
]


def test_single_char_bool_words_stay_strings():
    # Psych resolves y/n as bool; PyYAML does not. Ours follows
    # PyYAML here (the Python reference), overriding the resolver's
    # single-char bool verdicts.
    assert pyml.safe_load("a: y") == {"a": "y"}
    assert pyml.safe_load("a: n") == {"a": "n"}
    assert pyml.safe_load("a: Y") == {"a": "Y"}
    assert pyml.safe_load("a: yes") == {"a": True}


def test_merge_keys():
    ours = pyml.safe_load("a: 1\n<<: {b: 2}\n")
    assert ours == {"a": 1, "b": 2}  # existing keys win
    ours = pyml.safe_load("<<: [{a: 1, x: 0}, {b: 2, x: 9}]\n")
    assert ours == {"a": 1, "x": 0, "b": 2}  # first merge wins


def test_anchor_identity():
    doc = pyml.safe_load("a: &x [1]\nb: *x\nc: &y {k: v}\nd: *y\n")
    assert doc["a"] is doc["b"]
    assert doc["c"] is doc["d"]


def test_timestamps():
    # differential: PyYAML's own construct_yaml_timestamp semantics
    forms = [
        "2001-12-14 21:59:43.10 -05:00",
        "2001-12-14T21:59:43.10Z",
        "2001-12-14 21:59:43.10",
        "2001-12-14t21:59:43.10-05:00",
        "2001-12-14",
        "2001-12-14 21:59:43",
    ]
    for f in forms:
        assert pyml.safe_load(f) == pyyaml.safe_load(f), f
    assert pyml.safe_load("2001-12-14 21:59:43.10 -05:00").utcoffset() == \
        dt.timedelta(hours=-5)
    # a QUOTED date is a string (implicit-only host-side shaping)
    assert pyml.safe_load('"2001-12-14"') == "2001-12-14"


def test_special_floats():
    doc = pyml.safe_load("a: .nan\nb: .inf\nc: -.Inf")
    assert math.isnan(doc["a"])
    assert doc["b"] == math.inf
    assert doc["c"] == -math.inf


def test_multidocument():
    docs = list(pyml.safe_load_all("--- 1\n--- two\n---\n- 3\n"))
    assert docs == [1, "two", [3]]
    assert pyml.safe_load("--- 1\n--- 2\n") == 1


def test_empty_stream():
    assert pyml.safe_load("") is None
    assert pyml.safe_load("# just a comment\n") is None
    assert list(pyml.safe_load_all("")) == []


def test_parse_error():
    with pytest.raises(ParseError) as ei:
        pyml.safe_load("a: [1, 2")
    assert ei.value.line >= 1
    with pytest.raises(ParseError):
        pyml.safe_load("a: *undefined")


def test_bytes_and_file_like():
    import io
    assert pyml.safe_load(b"k: v") == {"k": "v"}
    assert pyml.safe_load(io.StringIO("k: v")) == {"k": "v"}
    assert pyml.safe_load(io.BytesIO(b"k: v")) == {"k": "v"}


def test_neutral_surface():
    assert yeptris.load("k: v") == {"k": "v"}
    assert list(yeptris.load_all("--- 1\n--- 2\n")) == [1, 2]


def test_undefined_alias_composer_parity():
    # The engine's scanner carries any alias name; the composer's
    # rejection is the load's error (PyYAML ComposerError parity).
    with pytest.raises(ParseError) as ei:
        pyml.safe_load("bad: *missing")
    assert "found undefined alias 'missing'" in str(ei.value)
    # the record walk (the non-columnar fallback) matches
    with pytest.raises(ParseError):
        yeptris._loader.load_all(b"bad: *missing")
    # an anchor legitimately holding null still resolves
    assert pyml.safe_load("a: &x null\nb: *x\n") == {"a": None, "b": None}
    # a forward reference rejects too
    with pytest.raises(ParseError):
        pyml.safe_load("[*a, &a 1]")


# --- PyYAML constructor parity: !!set / !!omap (issue #166) --------------
# The engine's drains surface the collection's explicit tag text, so the
# constructor can re-tag containers the way SafeLoader's registered
# constructors do: !!set builds a Python set of the mapping's keys,
# !!omap/!!pairs build a LIST OF PAIRS (PyYAML 6 constructs pairs, not
# an OrderedDict — its SafeDumper has no OrderedDict representer either).

def test_set_loads_as_a_set():
    assert pyml.safe_load("!!set {a, b, c}") == {"a", "b", "c"}
    assert pyml.safe_load("!!set\n? a\n? b") == {"a", "b"}
    assert pyml.safe_load("!!set {}") == set()
    assert pyml.safe_load("!!set {1, 2}") == {1, 2}
    assert pyml.safe_load("!!set {a: x, b: y}") == {"a", "b"}  # values discarded
    assert pyyaml.safe_load("!!set {a: x, b: y}") == {"a", "b"}


def test_set_roundtrip():
    # yaml.dump({1,2}) -> yaml.load -> the same set (was: a list)
    for value in ({"a", "b"}, {1, 2}, set()):
        assert pyml.safe_load(pyml.safe_dump(value)) == value


def test_set_nested_and_aliased():
    assert pyml.safe_load(pyml.safe_dump({"s": {"b", "a"}})) == {"s": {"a", "b"}}
    doc = pyml.safe_load("k: &s !!set {p}\nj: *s\n")
    assert doc == {"k": {"p"}, "j": {"p"}}
    assert doc["k"] is doc["j"]


def test_set_merge_keys_flatten():
    # PyYAML's construct_mapping flattens merges for set mappings too:
    # the merge source contributes its KEYS
    assert pyml.safe_load("!!set {<<: {m: 1}, b: 2}") == {"m", "b"}
    assert pyyaml.safe_load("!!set {<<: {m: 1}, b: 2}") == {"m", "b"}


def test_set_on_a_sequence_is_a_constructor_error_parity():
    # construct_yaml_set routes through construct_mapping: a sequence
    # node is the constructor error, in both implementations
    for text in ("!!set [a, b]", "!!set []"):
        with pytest.raises(pyyaml.constructor.ConstructorError):
            pyyaml.safe_load(text)
        with pytest.raises(ConstructorError):
            pyml.safe_load(text)
        with pytest.raises(ConstructorError):
            yeptris._loader.load_all(text)  # the record walk matches


def test_set_unhashable_key_is_a_constructor_error_parity():
    with pytest.raises(pyyaml.constructor.ConstructorError):
        pyyaml.safe_load("!!set {[], x}")
    with pytest.raises(ConstructorError):
        pyml.safe_load("!!set {[], x}")


def test_omap_loads_as_pairs():
    assert pyml.safe_load("!!omap [a: 1, b: 2]") == [("a", 1), ("b", 2)]
    assert pyml.safe_load("!!omap\n- a: 1\n- b: 2") == [("a", 1), ("b", 2)]
    assert pyml.safe_load("!!pairs [a: 1]") == [("a", 1)]
    # !!pairs shares the constructor; !!omap must NOT read as an
    # OrderedDict (PyYAML 6 builds a pairs list — pinned divergence
    # from the issue text's OrderedDict expectation)
    assert not isinstance(pyml.safe_load("!!omap [a: 1]"), dict)
    assert pyyaml.safe_load("!!omap [a: 1]") == [("a", 1)]


def test_omap_member_shape_errors_parity():
    # every member must be a mapping of length 1 (compose-time in
    # PyYAML, placement-time here — same rejections, same problem
    # strings)
    for text, fragment in (
        ("!!omap [a]", "expected a mapping of length 1, but found scalar"),
        ("!!omap [[a]]", "expected a mapping of length 1, but found sequence"),
        ("!!omap [{}]", "expected a single mapping item, but found 0 items"),
        ("!!omap [{a: 1, b: 2}]", "expected a single mapping item, but found 2 items"),
        ("!!omap {a: 1}", "expected a sequence, but found mapping"),
    ):
        with pytest.raises(pyyaml.constructor.ConstructorError):
            pyyaml.safe_load(text)
        with pytest.raises(ConstructorError) as ei:
            pyml.safe_load(text)
        assert fragment in str(ei.value), text
        with pytest.raises(ConstructorError):
            yeptris._loader.load_all(text)  # the record walk matches


def test_omap_aliased_member_pair_extraction():
    # PyYAML resolves the alias node first: a single-pair anchor
    # contributes its pair twice
    text = "!!omap [&m {x: 1}, *m]"
    ours = pyml.safe_load(text)
    theirs = pyyaml.safe_load(text)
    assert ours == theirs == [("x", 1), ("x", 1)]
    with pytest.raises(ConstructorError):
        pyml.safe_load("!!omap [&m {x: 1, y: 2}, *m]")
    with pytest.raises(pyyaml.constructor.ConstructorError):
        pyyaml.safe_load("!!omap [&m {x: 1, y: 2}, *m]")


def test_omap_merge_key_is_not_flattened_parity():
    # the omap path iterates the composed pairs directly — merges do
    # not flatten there (a sole merge pair has no constructor)
    with pytest.raises(ConstructorError) as ei:
        pyml.safe_load("!!omap [{<<: {z: 1}, b: 2}]")
    assert "found 2 items" in str(ei.value)
    with pytest.raises(pyyaml.constructor.ConstructorError):
        pyyaml.safe_load("!!omap [{<<: {z: 1}, b: 2}]")
    with pytest.raises(ConstructorError) as ei:
        pyml.safe_load("!!omap [{<<: {z: 9}}]")
    assert "could not determine a constructor for the tag " \
        "'tag:yaml.org,2002:merge'" in str(ei.value)
    with pytest.raises(pyyaml.constructor.ConstructorError):
        pyyaml.safe_load("!!omap [{<<: {z: 9}}]")


SET_OMAP_CASES = [
    # (yaml, expected) — the columns walk and the record walk must
    # construct the same values (the lockstep law)
    ("!!set {a, b, c}", {"a", "b", "c"}),
    ("!!set\n? a\n? b", {"a", "b"}),
    ("!!set {}", set()),
    ("!!set {1, 2}", {1, 2}),
    ("!!set {a: x, b: y}", {"a", "b"}),
    ("!!set {<<: {m: 1}, b: 2}", {"m", "b"}),
    ("a: !!set {q}\nb: [!!set {r}]", {"a": {"q"}, "b": [{"r"}]}),
    ("!!omap [a: 1, b: 2]", [("a", 1), ("b", 2)]),
    ("!!omap\n- a: 1\n- b: 2", [("a", 1), ("b", 2)]),
    ("!!pairs [a: 1]", [("a", 1)]),
    ("outer: !!set {a}\ninner: !!omap [x: 1]",
     {"outer": {"a"}, "inner": [("x", 1)]}),
    ("!!omap [&m {x: 1}, *m]", [("x", 1), ("x", 1)]),
    ("k: &s !!set {p}\nj: *s", {"k": {"p"}, "j": {"p"}}),
]


@pytest.mark.parametrize("text,expected", SET_OMAP_CASES)
def test_set_omap_both_walks_agree(text, expected):
    assert pyml.safe_load(text) == expected
    assert yeptris._loader.load_all(text)[0] == expected
