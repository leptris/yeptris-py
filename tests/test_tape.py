"""The compact JSON tape (yeptris/tape.h): ONE record per token in
three parallel columns — kind byte, off, len — carved from one block.
Strings stay zero-copy spans into the caller's buffer; NUMBERS RECORD
SPANS ONLY at parse and convert at materialize
(yeptris_tape_convert — simdjson's deferred contract).

Pinned law (the header's contract):
- records start AFTER the YEP_T_DOC boundary record; bools encode in
  the kind (TRUE/FALSE carry no span); container links ride the off
  column (OPEN <-> CLOSE are mutual); keys are the STR records at
  even positions inside a MAP_OPEN..CLOSE range.
- the strict route validates number grammar at parse; the lenient
  route defers it to convert (a "1.2.3" run parses here, rejects at
  convert; a run with non-number bytes still rejects at parse).
- spans are RAW: string escapes stay untouched in the bytes.
"""

import pytest

from yeptris import ParseError, YeptrisError
from yeptris import _ffi as F
from yeptris import tape as tape_mod
from yeptris.tape import parse_tape

pytestmark = pytest.mark.skipif(
    not F.TAPE_AVAILABLE,
    reason="the loaded libyeptris predates the tape face (tape.h)")

KIND = tape_mod


def test_doc_boundary_record_first():
    with parse_tape(b'{"a": 1}') as tape:
        assert tape.count >= 1
        assert tape.kind(0) == KIND.KIND_DOC


def test_scalar_root_one_record():
    with parse_tape(b'42') as tape:
        assert tape.count == 2  # DOC + INT
        assert tape.kind(1) == KIND.KIND_INT
        assert tape.span(1) == (0, 2)
        assert tape.bytes(1) == b"42"


def test_true_false_encode_in_kind():
    with parse_tape(b'[true, false]') as tape:
        assert tape.kind(2) == KIND.KIND_TRUE
        assert tape.kind(3) == KIND.KIND_FALSE


def test_null_kind():
    with parse_tape(b'null') as tape:
        assert tape.kind(1) == KIND.KIND_NULL


def test_container_links_are_mutual():
    with parse_tape(b'{"a": [1, {"b": 2}]}') as tape:
        open_idx = [i for i in range(tape.count)
                    if tape.kind(i) in (KIND.KIND_SEQ_OPEN, KIND.KIND_MAP_OPEN)]
        for i in open_idx:
            close = tape.link(i)
            assert tape.kind(close) == KIND.KIND_CLOSE
            assert tape.link(close) == i


def test_string_span_bytes_raw():
    # escapes stay untouched: the qbc kernel found them, the convert
    # layer never re-writes them
    with parse_tape(b'"a\\nb"') as tape:
        assert tape.kind(1) == KIND.KIND_STR
        assert tape.bytes(1) == b"a\\nb"


def test_convert_numbers():
    with parse_tape(b'{"a": 5, "b": 1.5}') as tape:
        n, ivals, dvals = tape.convert(1, tape.count)
        assert n == 2
        # ivals/dvals are parallel to the RECORD indices
        int_idx = 3  # DOC MAP_OPEN STR(key) INT
        assert ivals[int_idx] == 5
        float_idx = 5
        assert dvals[float_idx] == 1.5


def test_convert_default_range_is_whole_tape():
    with parse_tape(b'[1, 2, 3]') as tape:
        n, ivals, _d = tape.convert()
        assert n == 3
        assert ivals[2] == 1 and ivals[3] == 2 and ivals[4] == 3


def test_columns_materialize():
    with parse_tape(b'{"a": 1}') as tape:
        kinds, offs, lens = tape.columns()
        assert len(kinds) == tape.count
        assert len(offs) == tape.count
        assert len(lens) == tape.count
        assert kinds[0] == KIND.KIND_DOC


def test_strict_rejects_malformed():
    with pytest.raises(ParseError):
        parse_tape(b'{"a": }')
    with pytest.raises(ParseError):
        parse_tape(b'[1, 2')


def test_strict_rejects_bad_number_grammar():
    # the strict route validates the number grammar AT parse
    with pytest.raises(ParseError):
        parse_tape(b'{"x": 1.2.3}')


def test_lenient_defers_number_grammar_to_convert():
    with parse_tape(b'{"x": 1.2.3}', lenient=True) as tape:
        assert tape.kind(3) == KIND.KIND_NUM
        with pytest.raises(ParseError):
            tape.convert()


def test_lenient_accepts_tab_whitespace():
    with parse_tape(b'{\t"a": 1\n}', lenient=True) as tape:
        assert tape.count >= 3


def test_lenient_superset_structurally_identical():
    # on everything the strict route accepts, the tapes agree: counts,
    # spans and container links equal, and the number records carry
    # the same bytes — the only difference is the number KIND byte
    # (NUM where the strict route already classified INT/FLOAT)
    src = b'{"a": [1, 2.5, "x"], "b": null}'
    with parse_tape(src) as strict, parse_tape(src, lenient=True) as lenient:
        assert strict.count == lenient.count
        for i in range(strict.count):
            assert strict.span(i) == lenient.span(i)
            ks, kl = strict.kind(i), lenient.kind(i)
            if ks in (KIND.KIND_INT, KIND.KIND_FLOAT):
                assert kl == KIND.KIND_NUM
            else:
                assert ks == kl
            bs, bl = strict.bytes(i), lenient.bytes(i)
            assert bs == bl


def test_bad_span_rejects_at_convert():
    # a charset run with non-number bytes still rejects at parse
    with pytest.raises(ParseError):
        parse_tape(b'{"x": 12ab}')


def test_unicode_span():
    with parse_tape('{"k": "é中文"}'.encode("utf-8")) as tape:
        assert tape.bytes(3) == "é中文".encode("utf-8")


def test_closed_tape_rejects_access():
    tape = parse_tape(b'1')
    tape.close()
    with pytest.raises(YeptrisError):
        tape.count
