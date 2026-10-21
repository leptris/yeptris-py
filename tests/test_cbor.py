"""The Python CBOR surface (TODO.cbor: the py face of RFC 8949)."""

import yeptris.cbor as cbor

import pytest

pytestmark = pytest.mark.skipif(
    not cbor.available(), reason="the vendored libyeptris predates the CBOR codec"
)

VALUES = [
    None,
    True,
    False,
    0,
    -1,
    2**31,
    -2**31,
    2.5,
    "",
    "text",
    [],
    {},
    {"a": [1, 2.5, None, True, "x"], "b": {"nested": -3}},
    [[[[ "deep" ]]]],
    {"users": [{"id": i, "p": {"q": {"r": "s"}}} for i in range(1, 16)]},
    {"users": [{"id": i, "profile": {"age": i, "preferences": {"theme": "dark"}}} for i in range(1000)]},
]


@pytest.mark.parametrize("value", VALUES, ids=range(len(VALUES)))
def test_round_trip(value):
    assert cbor.load(cbor.dump(value)) == value


def test_canonical_bytes_match_the_reference_vector():
    # {"b": {"nested": -3}, "a": [1, 2.5, null, true, "x"]} — keys sort
    # bytewise (a < b); identical to the Ruby surface's lock
    assert cbor.dump({"b": {"nested": -3}, "a": [1, 2.5, None, True, "x"]}).hex() == (
        "a261618501f94100f6f561786162a1666e657374656422"
    )


def test_insertion_order_without_canonical():
    assert cbor.dump({"b": 1, "a": 2}, canonical=False).hex() == "a2616201616102"


def test_sequence_round_trip():
    items = [1, {"k": "v"}, [True], None, "tail"]
    assert cbor.load_all(cbor.dump_all(items)) == items


def test_empty_sequence_is_valid():
    assert cbor.load_all(b"") == []


def test_truncated_item_raises():
    with pytest.raises(F_parse_error()):
        cbor.load(b"\x1a\x00")


def F_parse_error():
    from yeptris._ffi import ParseError

    return ParseError


def test_dump_rejects_unsafe_types():
    with pytest.raises(Exception):
        cbor.dump(object())


class TestHostStringTyping:
    """#293: a host str shaped like an exponent stays a CBOR text
    string (compat-11 typing: the float law requires the dot and a
    signed exponent)."""

    @pytest.mark.parametrize("s", ["1e0", "10e9", "1.5e2", "2E5"])
    def test_exponent_strings_round_trip(self, s):
        enc = cbor.dump(s)
        assert s.encode() in enc
        assert cbor.load(enc) == s

    def test_issue_repro(self):
        assert cbor.load(cbor.dump("1e0")) == "1e0"
        assert cbor.load(cbor.dump("1.5")) == "1.5"
        assert cbor.load(cbor.dump("hello 1e0 world")) == "hello 1e0 world"

    def test_host_numbers_stay_numeric(self):
        assert cbor.load(cbor.dump(1)) == 1
        assert cbor.load(cbor.dump(1.5)) == 1.5
