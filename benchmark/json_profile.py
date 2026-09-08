#!/usr/bin/env python3
"""json_profile.py — the Python JSON referee (TODO.restructure/41).

The fair-benchmark rules from the Ruby campaign (milestone 77):
ORDER-ALTERNATING interleave (a fixed order biases the second runner
with cache warmth), min/median/mean over head-to-head rounds, and
GATE semantics — GATE=1.00 exits 1 when the mean ratio meets the
threshold (a loss fails CI).

Usage: python benchmark/json_profile.py [rounds=200]
Env:  YEPTRIS_NATIVE_CACHE=off — the token-cache A/B knob
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yeptris.json as yjson  # noqa: E402


def corpus() -> str:
    """The reference shape (152 KB / ~29k values), generated — the
    same recipe as the Ruby profile: wide maps of short keys with
    mixed scalar values."""
    rows = []
    for i in range(900):
        rows.append({
            "id": i, "name": f"item-{i}", "active": i % 2 == 0,
            "score": i * 0.25, "count": i,
            "tags": ["a", "b", "c"],
            "meta": {"origin": "gen", "weight": 1.5, "path": f"/x/{i}"},
        })
    return json.dumps({"items": rows, "total": len(rows)}, indent=None)


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    text = corpus()
    assert len(text) > 100_000
    warm = 5
    for _ in range(warm):
        json.loads(text)
        yjson.loads(text)

    yep, std = [], []
    for r in range(rounds):
        # alternate who runs first: a fixed order biases the second
        # runner with cache warmth (the milestone-77 lesson)
        if r % 2 == 0:
            t0 = time.perf_counter(); json.loads(text); std.append(time.perf_counter() - t0)
            t0 = time.perf_counter(); yjson.loads(text); yep.append(time.perf_counter() - t0)
        else:
            t0 = time.perf_counter(); yjson.loads(text); yep.append(time.perf_counter() - t0)
            t0 = time.perf_counter(); json.loads(text); std.append(time.perf_counter() - t0)

    def row(name, xs):
        return (f"{name:10s} min {min(xs)*1000:6.3f}  med "
                f"{statistics.median(xs)*1000:6.3f}  mean "
                f"{statistics.mean(xs)*1000:6.3f} ms")

    mean_ratio = statistics.mean(yep) / statistics.mean(std)
    h2h = sum(1 for a, b in zip(yep, std) if a < b)
    print(f"engine: {yjson.engine()}  corpus {len(text)//1024} KB  rounds {rounds}")
    print(row("yeptris", yep))
    print(row("json", std))
    print(f"mean ratio {mean_ratio:.3f}x  h2h {h2h}/{rounds}")

    gate = os.environ.get("GATE", "")
    if gate and not float(gate) > 0:
        gate = ""  # empty means unset
    if gate:
        threshold = float(gate)
        if mean_ratio >= threshold:
            print(f"GATE FAILED: mean ratio {mean_ratio:.3f}x >= {threshold}")
            return 1
        print(f"GATE PASSED: mean ratio {mean_ratio:.3f}x < {threshold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
