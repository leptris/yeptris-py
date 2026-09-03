"""Loader semantics: differential against PyYAML where the two must
agree, explicit pins where they diverge by design."""

import datetime as dt
import math

import pytest
import yaml as pyyaml

import yeptris
from yeptris import ParseError
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
