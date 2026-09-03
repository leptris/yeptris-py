#!/usr/bin/env python3
"""Honest numbers: yeptris vs PyYAML (pure) and CSafeLoader (libyaml),
same process, same inputs. Run: python3 bench.py [--quick]"""

import sys
import time

try:
    from yaml import CSafeLoader
except ImportError:
    CSafeLoader = None
import yaml

import yeptris
from yeptris import yaml as pyml


def scalar_heavy(n):
    rows = []
    for i in range(n):
        rows.append(f"key{i}: value {i} with text {i * 7}\n")
    return "".join(rows)


def flow_json(n):
    return "[" + ",".join(f'{{"id": {i}, "name": "row{i}", "ok": true, "score": {i}.5}}' for i in range(n)) + "]\n"


def block_mixed(depth_n):
    out = []
    for i in range(depth_n):
        out.append(f"section{i}:\n  title: Section {i}\n  items:\n    - one\n    - two\n    - three\n  meta: {{a: 1, b: [2, 3]}}\n")
    return "".join(out)


def run(loader, text, reps):
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        loader(text)
        best = min(best, time.perf_counter() - t0)
    return len(text) / best / 1e6


def main():
    quick = "--quick" in sys.argv
    reps = 5 if quick else 30
    n = 3000 if quick else 20000
    shapes = [
        ("scalar-heavy", scalar_heavy(n)),
        ("flow-json", flow_json(n)),
        ("block-mixed", block_mixed(n // 4)),
    ]
    print(f"{'shape':<14} {'yeptris':>10} {'PyYAML':>10} {'CSafe':>10}   MB/s (vs PyYAML pure / CSafe)")
    for name, text in shapes:
        ours = run(pyml.safe_load, text, reps)
        theirs = run(yaml.safe_load, text, reps)
        cs = run((lambda t: yaml.load(t, Loader=CSafeLoader)) if CSafeLoader else (lambda t: None), text, reps)
        base1 = f"{ours / theirs:5.1f}x" if theirs else "?"
        base2 = f"{ours / cs:5.1f}x" if CSafeLoader and cs else "  n/a"
        print(f"{name:<14} {ours:10.1f} {theirs:10.1f} {cs if cs else float('nan'):10.1f}   {base1} / {base2}")


if __name__ == "__main__":
    main()
