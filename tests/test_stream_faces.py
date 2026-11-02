"""The multi-document stream face (#886): one shared boundary corpus
drives PyYAML's law through a subprocess oracle, the facade, and both
native drain routes (record + columnar).

Pinned law (PyYAML 6.x):
- load_all/safe_load_all are LAZY generators — the call itself never
  parses; a bad document raises at ITERATION time.
- empty documents yield None ('---' with no content); a stream with
  zero documents ('', comments only) yields nothing.
- marker-less consecutive plain roots fold into ONE document
  ('1\n2\n' is one multiline plain scalar: ['1 2']).
- explicit '---' / '...' markers; directives ahead of the first
  '---'; a trailing '...' opens one final empty document.
- anchors never reach across documents (PyYAML's composer resets
  them per document).
- safe_load answers the FIRST document only — this binding's
  documented divergence: silently (PyYAML raises ComposerError on
  multi-document input).

Documented divergences (pinned per case, never silent):
- the engine parses a stream in ONE bulk drain, so an invalid stream
  raises at the first iteration with NO documents yielded — PyYAML's
  per-document parser yields the good prefix first.
- %YAML / %TAG directives BETWEEN documents: PyYAML accepts, the
  engine honors directives only ahead of the first document
  (carried engine gap).
- a stream that opens with '...': PyYAML raises, the engine answers
  zero documents.
- an implicit document after '...' ('--- 1', '...', '2'): the engine
  parses it (spec-legal), PyYAML rejects — the same divergence
  yeptris-ruby pins against psych.
"""

import io
import json
import subprocess
import sys
import types

import pytest
import yaml as pyyaml  # in-process only as a doc SOURCE for the dump side

import yeptris
from yeptris import _loader
from yeptris import _ffi as F
from yeptris import yaml as pyml
from yeptris.yaml import ComposerError, ParserError

# --- the shared corpus -------------------------------------------------
# (name, text, expected documents). Every entry parses under BOTH
# engines to exactly these documents (PyYAML's verdict is verified
# live through the subprocess oracle, so a drifted fixture fails
# against the reference, not just against us).
CORPUS = [
    ("explicit-markers", "--- 1\n--- two\n---\n- 3\n", [1, "two", [3]]),
    ("empty-stream", "", []),
    ("comments-only", "# just a comment\n", []),
    ("single-empty-doc", "---\n", [None]),
    ("consecutive-empty-docs", "---\n---\n", [None, None]),
    ("explicit-end-between-docs", "--- 1\n...\n--- 2\n", [1, 2]),
    ("end-then-explicit-doc", "--- 1\n--- 2\n...\n--- 3\n", [1, 2, 3]),
    ("implicit-doc-then-explicit", "a: 1\n---\nb: 2\n",
     [{"a": 1}, {"b": 2}]),
    ("plain-roots-fold", "1\n2\n", ["1 2"]),
    ("scalar-roots-explicit-boundary", "foo\n---\nbar\n", ["foo", "bar"]),
    ("flow-root-then-explicit", "[1, 2]\n---\n3\n", [[1, 2], 3]),
    ("tagged-empty-scalar", "--- 1\n--- !!str\n", [1, ""]),
    ("explicit-doc-then-empty-doc", "a: 1\n---\n...\n", [{"a": 1}, None]),
    ("leading-yaml-directive", "%YAML 1.1\n---\na: 1\n", [{"a": 1}]),
    ("leading-tag-directive", "%TAG !e! tag:x\n---\na: 1\n", [{"a": 1}]),
    ("nested-two-docs", "---\n- 1\n- {k: [2, 3]}\n---\nk: v\n",
     [[1, {"k": [2, 3]}], {"k": "v"}]),
    ("alias-and-anchor-one-doc", "a: &x [1]\nb: *x\n---\nc: 2\n",
     [{"a": [1], "b": [1]}, {"c": 2}]),
]

# error streams — the laziness/raise law, checked per side
ERR_CASES = [
    ("bad-mid-stream", "a: 1\n--- [1,\nb: 2\n"),
    ("bad-first-doc", "bad: [\n--- 2\n"),
    ("cross-doc-alias", "a: &x 1\n---\nb: *x\n"),
]

# pinned divergences — PyYAML's verdict is recorded by the oracle,
# ours asserted beside it (never silently carried)
DIV_CASES = [
    ("directives-between-docs",
     "%YAML 1.1\n---\na: 1\n%YAML 1.1\n---\nb: 2\n"),
    ("tag-directive-between-docs",
     "%YAML 1.1\n---\na: 1\n%TAG !e! tag:x\n---\nb: 2\n"),
    ("dots-only", "...\n"),
    ("implicit-after-end", "--- 1\n...\n2\n"),
    ("directive-without-document", "%YAML 1.1\n"),
]

# The reference oracle: PyYAML 6.x in a fresh interpreter (a child
# process, so no in-process import-order effects). Table mode answers
# per-case verdicts for the corpus; read mode re-reads ONE text (our
# dump_all output) through PyYAML's loader.
_ORACLE = r"""
import json, sys
import yaml

data = json.load(sys.stdin)
if isinstance(data, str):  # read mode
    docs, err = [], None
    try:
        for doc in yaml.safe_load_all(data):
            docs.append(doc)
    except Exception as e:
        err = type(e).__name__
    print(json.dumps({"docs": None if err else docs, "err": err}))
    sys.exit(0)

out = {}
for name, text in data:
    docs, err, yielded = [], None, None
    try:
        for doc in yaml.safe_load_all(text):
            docs.append(doc)
    except Exception as e:
        err = type(e).__name__
        yielded = docs
    item = {"all": None if err else docs, "all_err": err,
            "yielded": yielded}
    try:
        item["one"] = yaml.safe_load(text)
    except Exception as e:
        item["one"] = None
        item["one_err"] = type(e).__name__
    if err is None:
        try:
            item["dumped"] = yaml.safe_dump_all(docs)
        except Exception as e:
            item["dump_err"] = type(e).__name__
    out[name] = item
print(json.dumps(out))
"""


def _oracle_read(text: str):
    proc = subprocess.run([sys.executable, "-c", _ORACLE],
                          input=json.dumps(text), capture_output=True,
                          text=True, check=True)
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def oracle():
    table = [[n, t] for n, t, _ in CORPUS] + list(ERR_CASES) + list(DIV_CASES)
    proc = subprocess.run([sys.executable, "-c", _ORACLE],
                          input=json.dumps(table), capture_output=True,
                          text=True, check=True)
    return json.loads(proc.stdout)


# --- the boundary law, differential ------------------------------------

@pytest.mark.parametrize("name,text,expected", CORPUS,
                         ids=[c[0] for c in CORPUS])
def test_boundary_corpus(name, text, expected, oracle):
    theirs = oracle[name]
    # the corpus is law: PyYAML itself must still agree (drift alarm)
    assert theirs["all_err"] is None, \
        "fixture drifted: PyYAML rejects it (%s)" % theirs["all_err"]
    assert theirs["all"] == expected
    # the facade: same documents, same None placement
    ours = list(pyml.safe_load_all(text))
    assert ours == expected
    # the C-face differential: both native drain routes agree
    assert _loader.load_all(text) == ours
    if F.COLUMNS:
        assert _loader.load_all_columns(text) == ours


@pytest.mark.parametrize("name,text,expected", CORPUS,
                         ids=[c[0] for c in CORPUS])
def test_dump_all_roundtrip_corpus(name, text, expected, oracle):
    # ours reads ours, on every route
    ours_text = pyml.safe_dump_all(expected)
    assert list(pyml.safe_load_all(ours_text)) == expected
    assert yeptris.load_all(ours_text) == expected
    # the reference reads ours to the same documents (subprocess)
    reread = _oracle_read(ours_text)
    assert reread["err"] is None
    assert reread["docs"] == expected
    # and ours reads THEIR dump_all shape (PyYAML's '--- 2\n...\n'
    # trailing-dots form included)
    assert "dump_err" not in oracle[name]
    assert list(pyml.safe_load_all(oracle[name]["dumped"])) == expected


# --- the laziness law ---------------------------------------------------

def test_safe_load_all_is_a_lazy_generator():
    # PyYAML parity: a generator — and the call itself never parses
    gen = pyml.safe_load_all("bad: [")
    assert isinstance(gen, types.GeneratorType)
    assert isinstance(pyyaml.safe_load_all("bad: ["), types.GeneratorType)
    # the raise waits for iteration
    with pytest.raises(ParserError):
        list(gen)


def test_bad_mid_stream_raises_at_iteration(oracle):
    name, text = ERR_CASES[0]
    theirs = oracle[name]
    # PyYAML: ParserError, at iteration, after the good prefix yielded
    assert theirs["all_err"] == "ParserError"
    assert theirs["yielded"] == [{"a": 1}]
    # ours: the same family, ALSO at iteration time (calling alone
    # never raises — the generator above survived construction)
    got = []
    with pytest.raises(ParserError) as ei:
        for doc in pyml.safe_load_all(text):
            got.append(doc)
    assert ei.value.line >= 2  # the mark points at/past the bad document
    # documented divergence (one bulk drain): nothing is yielded
    # before the raise — PyYAML's per-document parser yields the
    # good prefix first (pinned above)
    assert got == []
    # the C-face routes raise the same family on the same input
    with pytest.raises(ParserError):
        _loader.load_all(text)
    if F.COLUMNS:
        with pytest.raises(ParserError):
            _loader.load_all_columns(text)


def test_bad_first_doc_raises_at_iteration(oracle):
    name, text = ERR_CASES[1]
    theirs = oracle[name]
    assert theirs["all_err"] == "ParserError"
    assert theirs["yielded"] == []
    with pytest.raises(ParserError):
        list(pyml.safe_load_all(text))


def test_aliases_do_not_reach_across_documents(oracle):
    name, text = ERR_CASES[2]
    theirs = oracle[name]
    # PyYAML's composer resets anchors per document: doc 1 yields,
    # doc 2's alias raises ComposerError
    assert theirs["all_err"] == "ComposerError"
    assert theirs["yielded"] == [{"a": 1}]
    with pytest.raises(ComposerError):
        list(pyml.safe_load_all(text))
    # within one document the alias resolves (the corpus's positive
    # case covers the identity), and the neutral surface agrees
    with pytest.raises(yeptris.ParseError):
        yeptris.load_all(text)


# --- safe_load: first document only -------------------------------------

def test_safe_load_first_document_only(oracle):
    # PyYAML refuses multi-document streams through safe_load
    assert oracle["implicit-doc-then-explicit"]["one_err"] == "ComposerError"
    assert oracle["explicit-markers"]["one_err"] == "ComposerError"
    # this binding's documented divergence: the first document, silently
    assert pyml.safe_load("a: 1\n---\nb: 2\n") == {"a": 1}
    assert pyml.safe_load("--- 1\n--- 2\n") == 1
    assert pyml.safe_load("--- 1\n--- two\n---\n- 3\n") == 1
    # a zero-document stream answers None on both sides
    assert oracle["empty-stream"]["one"] is None
    assert oracle["comments-only"]["one"] is None
    assert pyml.safe_load("") is None
    assert pyml.safe_load("# just a comment\n") is None
    # a single empty document answers None too
    assert pyml.safe_load("---\n") is None


# --- the pinned divergences ---------------------------------------------

def test_divergence_directives_between_documents(oracle):
    # PyYAML accepts directives between documents (the leading form is
    # corpus agreement); the engine honors them only ahead of the
    # first document — %YAML and %TAG both (carried engine gap)
    assert oracle["directives-between-docs"]["all"] == [{"a": 1}, {"b": 2}]
    assert oracle["tag-directive-between-docs"]["all"] == \
        [{"a": 1}, {"b": 2}]
    for _name, text in DIV_CASES[:2]:
        with pytest.raises(ParserError):
            list(pyml.safe_load_all(text))


def test_divergence_dots_only_stream(oracle):
    # PyYAML raises on a stream that opens with '...'; the engine
    # answers zero documents
    assert oracle["dots-only"]["all_err"] == "ParserError"
    assert list(pyml.safe_load_all(DIV_CASES[2][1])) == []


def test_divergence_implicit_doc_after_end_marker(oracle):
    # spec-legal implicit document after '...': the engine parses it,
    # PyYAML rejects (the divergence yeptris-ruby pins against psych)
    assert oracle["implicit-after-end"]["all_err"] == "ParserError"
    assert list(pyml.safe_load_all(DIV_CASES[3][1])) == [1, 2]


def test_divergence_directive_without_document(oracle):
    # both reject a directive that never reaches a '---'; the family
    # matches (ParserError), the wording is each engine's own
    assert oracle["directive-without-document"]["all_err"] == "ParserError"
    with pytest.raises(ParserError):
        list(pyml.safe_load_all(DIV_CASES[4][1]))


# --- the dump_all face ---------------------------------------------------

def test_dump_all_separator_shape():
    assert pyml.safe_dump_all([1, 2]) == "1\n---\n2\n"
    assert pyml.safe_dump_all([1]) == "1\n"
    assert pyml.safe_dump_all([]) == ""
    assert pyml.safe_dump_all([1, 2], explicit_start=True) == \
        "---\n1\n---\n2\n"


def test_dump_all_none_documents_roundtrip():
    # None placement survives the dump side too (PyYAML's
    # '1\n--- null\n---\nk: v\n' shape, ours with '---\n' separators)
    docs = [1, None, {"k": "v"}]
    text = pyml.safe_dump_all(docs)
    assert list(pyml.safe_load_all(text)) == docs
    assert list(pyyaml.safe_load_all(text)) == docs


def test_dump_all_writes_to_stream():
    out = io.StringIO()
    assert pyml.safe_dump_all([{"a": 1}, {"b": 2}], out) is None
    assert out.getvalue() == "a: 1\n---\nb: 2\n"
    assert list(pyyaml.safe_load_all(out.getvalue())) == \
        [{"a": 1}, {"b": 2}]


def test_dump_all_accepts_a_generator():
    text = pyml.safe_dump_all(d for d in ({"a": 1}, {"b": 2}))
    assert list(pyml.safe_load_all(text)) == [{"a": 1}, {"b": 2}]


def test_dump_all_is_the_load_all_mirror():
    docs = [{"a": 1}, [2, 3], None, "text", [None]]
    assert yeptris.load_all(yeptris.dump_all(docs)) == docs
    assert list(pyml.load_all(pyml.dump_all(docs))) == docs


# --- the two faces' shapes ----------------------------------------------

def test_neutral_surface_is_a_list_compat_face_is_a_generator():
    # the binding-native face returns a list (its documented shape);
    # the PyYAML-compat face returns a lazy generator
    assert isinstance(yeptris.load_all("--- 1\n--- 2\n"), list)
    assert yeptris.load_all("") == []
    assert yeptris.load("") is None
    assert isinstance(pyml.load_all("--- 1\n--- 2\n"), types.GeneratorType)
    assert isinstance(pyml.safe_load_all("--- 1\n--- 2\n"),
                      types.GeneratorType)
