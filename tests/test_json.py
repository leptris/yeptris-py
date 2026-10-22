"""The JSON surface's parity battery (TODO.restructure/41).

The oracle is stdlib json.loads — value-for-value (repr equality, so
NaN compares by spelling), reject-for-reject (ValueError family), and
type-for-type. Both engines are exercised: the native materializer
(when the opt-in extension is importable) and the strict-ffi walk.
"""

from __future__ import annotations

import json
import math

import pytest

from yeptris import json as yjson

VALID = [
    '{"a": 1e3}', '{"n": -9223372036854775808}',
    '{"b": 92233720368547758089999}', '[1, 2.5, -0.0]',
    '{"u": "\\u00e9\\ud83d\\ude00"}', 'true', 'false', 'null', '"x"',
    '[[[[1]]]]', '{"dup": 1, "dup": 2}',
    '{"nested": {"k": [1, {"j": 2.5e-3}]}}',
    '  { "spaced" : [ 1 , 2 ] }  ', '{"empty": {}, "earr": []}',
    '{"z": 0.1, "big": 1e308, "neg": -1.5E+3}', '{}', '[]', '-0.5e-2',
    '{"esc": "a\\/b\\"c\\n\\t\\\\f\\b\\r"}',
    # json.loads' non-standard constants (accepted by default)
    'NaN', 'Infinity', '-Infinity', '{"a": [NaN, Infinity]}',
]

# unmarked garbage between tokens (the index skip must catch it in
# the gap validation — TODO.restructure/47's design note)
GAP_GARBAGE = ['[x1]', '{"a": 1x}', '[ tru e]', '{a:1}', '{"a"::1}', '[1 2]']

INVALID = [
    '{"a":}', '[1,]', '{"a":1}{', 'nul', "'x'", '[1 2]', '{"a" 1}',
    '', '+1', '01', '[,]', '{"a": undefined}', '[tru]', '{"a": 1,}',
    'NAN', 'infinity', '-Inf', '{"a": -InfinityX}', 'Infinityx',
]


def _engines():
    out = [("loads", yjson.loads)]
    if yjson._ext is not None:
        out.append(("native", yjson._ext.loads))
    out.append(("strict-ffi", yjson._fallback_loads))
    return out


@pytest.mark.parametrize("name,fn", _engines())
@pytest.mark.parametrize("text", VALID)
def test_matches_stdlib(name, fn, text):
    want = json.loads(text)
    got = fn(text)
    assert repr(got) == repr(want), (name, text)


@pytest.mark.parametrize("name,fn", _engines())
@pytest.mark.parametrize("text", INVALID + GAP_GARBAGE)
def test_rejects_like_stdlib(name, fn, text):
    with pytest.raises(ValueError):
        json.loads(text)
    with pytest.raises(ValueError):
        fn(text)


@pytest.mark.parametrize("name,fn", _engines())
def test_types(name, fn):
    v = fn('{"i": 1, "f": 1.5, "big": 99999999999999999999999, "s": "x"}')
    assert type(v["i"]) is int and type(v["f"]) is float
    assert type(v["big"]) is int and type(v["s"]) is str


@pytest.mark.parametrize("name,fn", _engines())
def test_bytes_input(name, fn):
    assert fn(b'{"a": 1}') == {"a": 1}


@pytest.mark.parametrize("name,fn", _engines())
def test_error_type_is_json_decode_error(name, fn):
    with pytest.raises(json.JSONDecodeError):
        fn("[1,]")


def test_nan_semantics():
    v = yjson.loads('{"a": NaN}')
    assert math.isnan(v["a"])


def test_hooks_delegate_to_stdlib():
    assert yjson.loads('{"a": 1.5}', parse_float=str) == {"a": "1.5"}


def test_engine_report():
    assert yjson.engine() in ("native", "strict-ffi")


def test_no_extension_falls_back(monkeypatch):
    monkeypatch.setattr(yjson, "_ext", None)
    assert yjson.engine() == "strict-ffi"
    assert yjson.loads('{"a": 1e3}') == {"a": 1000.0}


class TestDumps:
    """dumps/dump parity with the Ruby binding's JSON.dump (#138)."""

    def test_dumps_compact_matches_stdlib_shape(self):
        import json as stdlib
        v = {"a": 1, "b": [1, 2.5, True, None, "x"], "c": {"d": []}}
        s = yjson.dumps(v)
        assert isinstance(s, str)
        assert stdlib.loads(s) == v
        assert s == stdlib.dumps(v, separators=(",", ":"), ensure_ascii=False)

    def test_dumps_roundtrips_through_loads(self):
        v = {"k": [1, 2, 3], "s": "text", "n": None, "f": 1.5, "b": False}
        assert yjson.loads(yjson.dumps(v)) == v

    def test_dumps_indented_form(self):
        import json as stdlib
        s = yjson.dumps({"a": 1}, compact=False)
        assert stdlib.loads(s) == {"a": 1}

    def test_dump_writes_file_object(self):
        import io
        fp = io.StringIO()
        out = yjson.dump({"z": 0}, fp)
        assert fp.getvalue() == '{"z":0}'
        assert out == '{"z":0}'

    def test_dumps_unicode(self):
        s = yjson.dumps({"k": "café"})
        assert yjson.loads(s) == {"k": "café"}

    def test_dumps_resolvable_shaped_strings_stay_text(self):
        # yeptris-py#141: a host str shaped like exponent notation rode
        # STYLE_PLAIN and the JSON writer resolved it as a float. The
        # dump lane quotes every host str (the Ruby dump's law), so no
        # resolvable shape can re-shape.
        for s in ("1e0", "10e9", "1.5e2", "yes", "010", "~", "null", "true"):
            assert yjson.dumps(s) == json.dumps(s), s
            assert yjson.loads(yjson.dumps(s)) == s, s

    def test_dumps_resolvable_shaped_keys_stay_text(self):
        s = yjson.dumps({"1e0": "1e0", "yes": [True, 1]})
        assert yjson.loads(s) == {"1e0": "1e0", "yes": [True, 1]}

    def test_dumps_scalars_unchanged(self):
        assert yjson.dumps(1) == "1"
        assert yjson.dumps(1.5) == "1.5"
        assert yjson.dumps(True) == "true"
        assert yjson.dumps(None) == "null"
