"""The compact JSON tape (tape.h, C 0.6.77): the columnar record face
and its deferred-number contract.

Pinned law (tape.h + the engine's TODO.restructure/85):
- ONE record per token, record 0 the DOC boundary; three parallel
  columns (kind, off, len); container links ride `off` as the matching
  CLOSE's record index.
- string spans stay RAW (escapes untouched — the qbc law); numbers
  record spans only and convert at materialize (``Tape.convert`` is
  the deferred contract's authority; ``Tape.values`` classifies the
  lenient NUM records at materialize time).
- the lenient route accepts a superset of the strict route's inputs;
  on everything strict accepts, the tapes are structurally identical.
"""

import json as stdlib_json

import pytest

from yeptris import _ffi as F
from yeptris import json as yj
from yeptris import tape
from yeptris._errors import ParseError, YeptrisError
from yeptris.tape import parse_tape

pytestmark = pytest.mark.skipif(
    not F.TAPE_AVAILABLE,
    reason="the loaded libyeptris predates the tape face (C 0.6.77)")


# --- the record layout -----------------------------------------------------

def test_record_layout_of_a_document():
    t = parse_tape('{"k": [1, 2.5, "s", true, null]}')
    try:
        assert t.count == 11
        # DOC, MAP_OPEN, STR(k), SEQ_OPEN, INT, FLOAT, STR, TRUE, NULL,
        # CLOSE(seq), CLOSE(map)
        assert list(t.kinds()) == [
            F.TAPE_DOC, F.TAPE_MAP_OPEN, F.TAPE_STR, F.TAPE_SEQ_OPEN,
            F.TAPE_INT, F.TAPE_FLOAT, F.TAPE_STR, F.TAPE_TRUE, F.TAPE_NULL,
            F.TAPE_CLOSE, F.TAPE_CLOSE]
        offs = t.offs()
        lens = t.lens()
        assert offs[1] == 10  # the map's matching CLOSE
        assert offs[3] == 9   # the seq's matching CLOSE
        assert lens[1] == 0 and lens[3] == 0  # containers carry no span
        assert t.source[offs[2]:offs[2] + lens[2]] == b"k"
        assert t.source[offs[4]:offs[4] + lens[4]] == b"1"
        assert t.source[offs[5]:offs[5] + lens[5]] == b"2.5"
    finally:
        t.close()


def test_count_len_and_source():
    src = '{"a": 1}'
    t = parse_tape(src)
    try:
        assert t.count == len(t) == 5
        assert t.source == src.encode()
    finally:
        t.close()


def test_scalar_roots_are_single_records():
    for doc, kind, value in [("42", F.TAPE_INT, 42),
                             ('"x"', F.TAPE_STR, "x"),
                             ("true", F.TAPE_TRUE, True),
                             ("null", F.TAPE_NULL, None),
                             ("-0.5e2", F.TAPE_FLOAT, -50.0)]:
        t = parse_tape(doc)
        try:
            assert list(t.kinds()) == [F.TAPE_DOC, kind]
            assert t.values() == value
        finally:
            t.close()


def test_empty_containers():
    t = parse_tape('{"a": [], "b": {}}')
    try:
        assert t.values() == {"a": [], "b": {}}
    finally:
        t.close()


# --- the raw-span law ------------------------------------------------------

def test_string_spans_keep_escapes_raw():
    t = parse_tape('{"a": "x\\ny"}')
    try:
        # records: DOC(0) MAP_OPEN(1) STR-key-a(2) STR-value(3) CLOSE(4)
        assert t.count == 5
        assert t.span(3) == b"x\\ny"  # the escape stays two raw bytes
        assert t.kinds()[3] == F.TAPE_STR
        # values() finishes the escape
        assert t.values() == {"a": "x\ny"}
    finally:
        t.close()


def test_span_rejects_container_records():
    t = parse_tape("[1]")
    try:
        with pytest.raises(ValueError):
            t.span(1)  # the SEQ_OPEN record carries a link
        with pytest.raises(ValueError):
            t.span(3)  # the CLOSE
        assert t.span(2) == b"1"
    finally:
        t.close()


def test_unicode_escapes_and_surrogate_pairs():
    t = parse_tape('"\\u00e9 \\ud83d\\ude00"')
    try:
        assert t.values() == "é \U0001f600"
    finally:
        t.close()


# --- values(): the whole-document materialization --------------------------

CORPUS = [
    ('{"k": [1, 2.5, "s", true, null]}', {"k": [1, 2.5, "s", True, None]}),
    ('[[1, [2]], {"a": {"b": []}}]', [[1, [2]], {"a": {"b": []}}]),
    ('{"n": -12, "f": -3.25e2, "z": 0, "zf": 0.0}',
     {"n": -12, "f": -325.0, "z": 0, "zf": 0.0}),
    ('"\\u00e9\\n\\t\\"" ', 'é\n\t"'),
    ('[]', []),
    ('{}', {}),
]


@pytest.mark.parametrize("doc, expected", CORPUS)
def test_values_matches_the_json_face(doc, expected):
    t = parse_tape(doc)
    try:
        assert t.values() == expected
        assert t.values() == yj.loads(doc)  # cross-engine parity
    finally:
        t.close()


def test_int64_overflow_rebuilds_exactly_from_text():
    big = "12345678901234567890123456789"
    t = parse_tape('{"big": %s}' % big)
    try:
        assert t.values() == {"big": int(big)}  # exact, not int64-truncated
        assert t.values() == stdlib_json.loads(t.source)
    finally:
        t.close()


def test_values_rejects_an_empty_document():
    with pytest.raises(ParseError):
        parse_tape("   ")


# --- the deferred-number contract ------------------------------------------

def test_convert_aligns_slots_to_record_indices():
    # records: DOC(0) MAP_OPEN(1) STR-a(2) INT(3) STR-b(4) STR-x(5)
    # STR-c(6) FLOAT(7) CLOSE(8) — ',' and ':' record nothing
    t = parse_tape('{"a": 7, "b": "x", "c": 2.5}')
    try:
        assert t.count == 9
        ivals, dvals = t.convert()
        assert ivals[3] == 7          # record 3: the INT span
        assert dvals[7] == 2.5        # record 7: the FLOAT span
        assert ivals[7] == 0          # untouched slots stay zero
        assert dvals[3] == 0.0
        # partial ranges answer the same absolute slots
        part_i, part_d = t.convert(0, 4)
        assert list(part_i) == list(ivals[0:4])
        assert list(part_d) == list(dvals[0:4])
    finally:
        t.close()


def test_convert_range_guards():
    t = parse_tape("42")
    try:
        with pytest.raises(ValueError):
            t.convert(0, 9)
        with pytest.raises(ValueError):
            t.convert(5, 1)
        ivals, dvals = t.convert()
        assert len(ivals) == len(dvals) == 2
        assert ivals[1] == 42
    finally:
        t.close()


def test_lenient_defers_number_grammar_to_materialize():
    t = parse_tape('{"n": 1.2.3}', lenient=True)
    try:
        # the STRUCTURAL checks passed at parse; the run recorded as NUM
        assert F.TAPE_NUM in list(t.kinds())
        with pytest.raises(ValueError):
            t.values()  # the deferred reject
        with pytest.raises(ValueError):
            t.convert()
    finally:
        t.close()


def test_lenient_accepts_tab_whitespace():
    t = parse_tape(b'{"a":\t1}', lenient=True)
    try:
        assert t.values() == {"a": 1}
    finally:
        t.close()


def test_lenient_matches_strict_structure_on_strict_input():
    doc = b'{"k": [1, 2.5, "s", true, null], "m": {"n": -3}}'
    strict = parse_tape(doc)
    lenient = parse_tape(doc, lenient=True)
    try:
        # spans and containers are identical; the number records differ
        # only in kind — the lenient route records every number as NUM
        # (the same bytes the strict INT/FLOAT records carry)
        assert list(strict.offs()) == list(lenient.offs())
        assert list(strict.lens()) == list(lenient.lens())
        number_class = {F.TAPE_INT: "n", F.TAPE_FLOAT: "n",
                        F.TAPE_NUM: "n"}
        assert [number_class.get(k, k) for k in strict.kinds()] == \
            [number_class.get(k, k) for k in lenient.kinds()]
        assert strict.values() == lenient.values()
    finally:
        strict.close()
        lenient.close()


# --- the strict route's rejections -----------------------------------------

def test_strict_rejects_malformed_json():
    for doc in (b"[1,", b'{"a": }', b"01", b'{"a" 1}'):
        with pytest.raises(ParseError):
            parse_tape(doc)


def test_str_and_bytes_input_forms():
    assert parse_tape("42").values() == 42
    assert parse_tape(b"42").values() == 42
    import io
    assert parse_tape(io.BytesIO(b"42")).values() == 42
    with pytest.raises(TypeError):
        parse_tape(42)


# --- the lifetime law ------------------------------------------------------

def test_close_is_idempotent_and_accessors_die_after():
    t = parse_tape('{"a": 1}')
    t.close()
    t.close()
    for probe in (t.kinds, t.offs, t.lens, lambda: t.span(1),
                  lambda: t.kind_at(1), t.values, t.convert):
        with pytest.raises(YeptrisError):
            probe()


def test_context_manager_closes():
    with parse_tape('{"a": 1}') as t:
        assert t.values() == {"a": 1}
    with pytest.raises(YeptrisError):
        t.values()


def test_index_guards():
    t = parse_tape("42")
    try:
        with pytest.raises(IndexError):
            t.kind_at(9)
        with pytest.raises(IndexError):
            t.span(2)
    finally:
        t.close()


def test_capability_gate_names_the_floor():
    # the module's _require path: an unavailable engine names the floor
    assert tape.available() == bool(F.TAPE_AVAILABLE)
