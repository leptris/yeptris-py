"""The binary contract (PyYAML parity; the yeptris-ruby#168 port):
bytes dump as !!binary base64 blocks byte-identical to PyYAML, and
both load spellings decode back to bytes."""

import base64

import pytest

import yeptris


PAYLOAD = b"\xb0\x98abc"


def test_dump_dict_form():
    expected = "b: !!binary |\n  " + base64.b64encode(PAYLOAD).decode() + "\n"
    assert yeptris.dump({"b": PAYLOAD}) == expected


def test_dump_root_and_list_forms():
    expected = "!!binary |\n  " + base64.b64encode(b"abc").decode() + "\n"
    assert yeptris.dump(b"abc") == expected
    expected_list = "- !!binary |\n  eA==\n- !!binary |\n  eXk=\n"
    assert yeptris.dump([b"x", b"yy"]) == expected_list


def test_round_trip_all_shapes():
    for case in ({"b": PAYLOAD}, PAYLOAD, [b"x", b"yy"], {"k": {"n": b"zz"}}):
        out = yeptris.dump(case)
        assert yeptris.load(out) == case


def test_load_both_spellings():
    assert yeptris.load("k: !!binary YWJj") == {"k": b"abc"}
    assert yeptris.load("k: !!binary |\n  YWJq\n") == {"k": b"abj"}


def test_pyyaml_cross_compatibility():
    pytest.importorskip("yaml")
    import yaml

    for case in ({"b": PAYLOAD}, PAYLOAD, [b"x", b"yy"]):
        ours = yeptris.dump(case)
        assert ours == yaml.dump(case)
        assert yeptris.load(yaml.dump(case)) == case
        assert yaml.safe_load(ours) == case


def test_pyyaml_quote_style_parity():
    """PyYAML's emitter rules: bare y/n stay plain (not bools there),
    ambiguity words single-quote (double only when escapes are
    required). Pinned against the reference directly."""
    pytest.importorskip("yaml")
    import yaml

    cases = [
        {"y": 1, "n": 2, "yes": 3, "on": 4},
        ["y", "n", "yes", "true", "off", "null", "~"],
        {"k": "it's a #test"},
        {"k": 5014},
        {"k": ": leading"},
        {"k": "ends: "},
    ]
    for case in cases:
        assert yeptris.dump(case) == yaml.dump(case), case
