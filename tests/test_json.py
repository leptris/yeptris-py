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
