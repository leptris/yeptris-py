"""Merge keys ('<<'): PyYAML semantics pinned as the bar, in BOTH
walks. The audit found zero merge tests and a walk divergence — the
columns walk dropped a non-mapping merge value silently while the
record walk kept the literal '<<' key: same input, different output
by route. Every case here runs the columnar walk AND the record walk
and demands lockstep, and every semantics case is differential
against the installed PyYAML."""

import pytest
import yaml as pyyaml
import yaml.constructor as pyyaml_constructor

from yeptris import yaml as pyml
from yeptris.yaml import ComposerError, ConstructorError, YAMLError
from yeptris import _loader

# every case goes through BOTH walks; PyYAML is consulted for the
# expected outcome (value or exception class) automatically
CASES = [
    # (yaml, expected) — expected None => both implementations must raise
    ("<<: {a: 1}\nb: 2\n", {"a": 1, "b": 2}),
    ("<<: {}\nb: 2\n", {"b": 2}),               # empty mapping: a no-op
    ("<<: []\nb: 2\n", {"b": 2}),               # empty sequence: a no-op
    ("x: &m {a: 1}\n<<: *m\nb: 2\n",            # alias-to-mapping resolves
     {"x": {"a": 1}, "a": 1, "b": 2}),
    ("x: &e {}\n<<: *e\nb: 2\n", {"x": {}, "b": 2}),
    ("<<: [{a: 1, x: 0}, {b: 2, x: 9}]\n",      # first merge wins
     {"a": 1, "x": 0, "b": 2}),
    ("<<: [{a: 1}, {b: 2}]\nc: 3\n", {"a": 1, "b": 2, "c": 3}),
    ("x: &a {p: 1}\ny: &b {q: 2}\n<<: [*a, *b]\n",
     {"x": {"p": 1}, "y": {"q": 2}, "p": 1, "q": 2}),
    ("a: 1\n<<: {a: 9, b: 2}\n", {"a": 1, "b": 2}),   # existing keys win
    ("m:\n  <<: {n: 1}\n  k: 2\n", {"m": {"n": 1, "k": 2}}),  # nested
    ('\'<<\': {a: 1}\n', {"<<": {"a": 1}}),     # a quoted '<<' is literal
    # non-mapping merge values: PyYAML raises ConstructorError, and so
    # must we — in both walks (the divergence the audit found)
    ("<<: 5\nb: 2\n", None),
    ("<<: abc\nb: 2\n", None),
    ("x: &s 5\n<<: *s\nb: 2\n", None),          # alias-to-scalar
    ("<<: [1, 2]\n", None),                     # sequence of non-mappings
    ("<<: [{a: 1}, 2]\n", None),                # one bad item in the list
]


def _pyyaml_outcome(text):
    try:
        return pyyaml.safe_load(text), None
    except Exception as e:
        return None, e


def _ours_outcome(walk, text):
    try:
        return walk(text)[0], None
    except Exception as e:
        return None, e


@pytest.mark.parametrize("text,expected", CASES)
def test_merge_lockstep_and_pyyaml_parity(text, expected):
    ours, ours_err = _ours_outcome(_loader.load_all_columns, text)
    ours_rec, ours_rec_err = _ours_outcome(_loader.load_all, text)
    theirs, theirs_err = _pyyaml_outcome(text)

    # 1. the two walks agree — value or exception class, no silent drift
    # (the mark text legitimately differs: the columnar drain carries
    # no positions, the record drain does — the VERDICT must match)
    if ours_err or ours_rec_err:
        assert ours_err is not None and ours_rec_err is not None
        assert type(ours_err) is type(ours_rec_err)
    else:
        assert ours == ours_rec, f"walk divergence: {ours!r} vs {ours_rec!r}"

    # 2. PyYAML is the bar: same outcome shape
    if theirs_err is not None:
        assert ours_err is not None, \
            f"PyYAML raised {type(theirs_err).__name__}, we returned {ours!r}"
        assert type(ours_err).__name__ == type(theirs_err).__name__
    else:
        assert theirs == expected, "the fixture disagrees with PyYAML itself"
        assert ours == expected, \
            f"expected {expected!r}, got {ours!r} (pyyaml: {theirs!r})"


@pytest.mark.parametrize("text,fragment", [
    ("<<: 5\n", "expected a mapping or list of mappings for merging"),
    ("<<: abc\n", "expected a mapping or list of mappings for merging"),
    ("x: &s 5\n<<: *s\n", "expected a mapping or list of mappings for merging"),
    ("<<: [1, 2]\n", "expected a mapping for merging"),
    ("<<: [{a: 1}, 2]\n", "expected a mapping for merging"),
])
def test_merge_error_message_matches_pyyaml(text, fragment):
    # the problem string is PyYAML's own (installed 6.0.2 wording)
    with pytest.raises(ConstructorError) as ei:
        pyml.safe_load(text)
    assert fragment in str(ei.value)
    with pytest.raises(pyyaml_constructor.ConstructorError) as ti:
        pyyaml.safe_load(text)
    assert fragment in str(ti.value)


def test_merge_error_is_a_yaml_error():
    with pytest.raises(YAMLError):
        pyml.safe_load("<<: 5")


def test_merge_error_is_still_the_legacy_parse_error():
    # compat law: pre-taxonomy code catching yeptris.ParseError keeps
    # working for the new constructor rejections too
    import yeptris
    with pytest.raises(yeptris.ParseError):
        pyml.safe_load("<<: 5")


def test_merge_error_carries_marks_where_the_walk_has_them():
    with pytest.raises(ConstructorError) as ei:
        pyml.safe_load("a: 1\n<<: 5\n")     # the record walk: line 2
    assert ei.value.line == 2
    assert "in line 2" in str(ei.value)


def test_multidoc_merge_chaining():
    # anchors are per-document; each document's merge resolves against
    # its OWN anchors (multi-doc chaining, load_stream oracle)
    text = (
        "---\n"
        "x: &m {p: 1}\n"
        "<<: *m\n"
        "---\n"
        "y: &n {q: 2}\n"
        "<<: *n\n"
    )
    ours = list(pyml.safe_load_all(text))
    theirs = list(pyyaml.safe_load_all(text))
    assert ours == [{"x": {"p": 1}, "p": 1}, {"y": {"q": 2}, "q": 2}]
    assert ours == theirs
    # both walks, same stream
    assert _loader.load_all(text) == _loader.load_all_columns(text)


def test_merge_across_documents_does_not_leak():
    # a later document cannot merge an earlier one's anchors: doc 2's
    # `<<: *m` is an UNDEFINED alias there — PyYAML's composer rejects
    # it, and so must we
    import yaml.composer as pyyaml_composer

    text = "---\nx: &m {p: 1}\nq: 1\n---\n<<: *m\n"
    # doc 1 alone is ordinary
    assert pyml.safe_load("---\nx: &m {p: 1}\nq: 1\n") == {"x": {"p": 1}, "q": 1}
    # the full stream: the second document's alias must NOT resolve
    # into document 1's anchors
    with pytest.raises(pyyaml_composer.ComposerError):
        list(pyyaml.safe_load_all(text))
    with pytest.raises(ComposerError):
        list(pyml.safe_load_all(text))
    with pytest.raises(ComposerError):
        list(_loader.load_all_columns(text))
