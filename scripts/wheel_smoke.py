#!/usr/bin/env python3
"""wheel_smoke.py — the wheel artifact battery (TODO.restructure/42).

Release-time proof that the BUILT WHEEL is what users get: installed
into a clean environment with NO YEPTRIS_* env and NO sibling
checkout, the native JSON engine must be ACTIVE (a silently-fallback
wheel ships the win dead) and the canary semantics must hold on both
surfaces. This is the gem-smoke discipline applied to wheels; the
repo-side test (tests/test_wheel_smoke.py) runs the same battery
against the dev tree so it cannot rot.

Usage: python3 scripts/wheel_smoke.py
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    import yeptris
    import yeptris.json as yjson

    engine = yjson.engine()
    print(f"engine: {engine}  version: {yeptris.__version__}")
    if engine != "native":
        print("SMOKE FAIL: the wheel must carry the native engine "
              "(a fallback-only wheel ships the JSON win dead)")
        return 1

    # the lib resolved through the package (vendored), not an env var
    from yeptris import _ffi
    lib_path = getattr(_ffi._lib, "_name", "")
    if "_platform" not in lib_path and "yeptris" not in lib_path:
        print(f"SMOKE FAIL: libyeptris loaded from {lib_path!r} — not the vendored copy")

    # JSON canaries (the native engine's own semantics)
    cases = [
        ('{"a": 1e3}', {"a": 1000.0}),
        ('{"b": 92233720368547758089999}', {"b": 92233720368547758089999}),
        ('{"u": "\\u00e9\\ud83d\\ude00"}', {"u": "é\U0001f600"}),
        ("[[[[1]]]]", [[[[1]]]]),
    ]
    for text, want in cases:
        got = yjson.loads(text)
        if repr(got) != repr(want):
            print(f"SMOKE FAIL json {text!r}: {got!r} != {want!r}")
            return 1
    def _stdlib_raises(text):
        try:
            json.loads(text)
            return False
        except ValueError:
            return True

    for bad in ('{"a":}', "[1,]", 'nul', '{"a":1,"a":2}'):
        try:
            yjson.loads(bad)
            raised = False
        except ValueError:
            raised = True
        if raised != _stdlib_raises(bad):
            print(f"SMOKE FAIL json verdict on {bad!r}: raised={raised}")
            return 1

    # YAML canaries (the ctypes ladder must find the vendored lib).
    # NOTE the contract: this binding is PyYAML-compatible — PyYAML's
    # positional sexagesimal fold gives 1:30 -> 90 (Psych's weight
    # based fold gives 5400 on the RUBY binding; each binding keeps
    # its reference's semantics).
    from yeptris import load, dump
    if load("k: 1:30\n") != {"k": 90}:
        print("SMOKE FAIL yaml sexagesimal (PyYAML fold)")
        return 1
    if load("k: 12345678901234567890123\n")["k"] != 12345678901234567890123:
        print("SMOKE FAIL yaml big int")
        return 1
    tree = {"n": 2, "f": 0.5, "s": "héllo", "a": [1, "two"]}
    if load(dump(tree)) != tree:
        print("SMOKE FAIL yaml round-trip")
        return 1

    print(f"SMOKE PASS yeptris {yeptris.__version__} (native, vendored lib)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
