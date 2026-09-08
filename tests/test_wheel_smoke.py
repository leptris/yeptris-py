"""The wheel-smoke battery's anti-rot (TODO.restructure/42).

Release workflows run scripts/wheel_smoke.py against the INSTALLED
wheel (a clean venv, no env vars); this test runs the same battery
against the dev tree so the battery itself cannot rot. Skips with a
reason when the native engine is not built — the pure tree cannot
smoke what it does not carry.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "wheel_smoke.py"


@pytest.mark.skipif(
    os.environ.get("YEPTRIS_SMOKE_SKIP", "") == "1",
    reason="explicitly skipped",
)
def test_wheel_smoke_battery_against_dev_tree():
    import yeptris.json as yjson

    if yjson.engine() != "native":
        pytest.skip("native engine not built in this tree — release CI smokes the wheel")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPT.parent.parent)
    out = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=env,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "SMOKE PASS" in out.stdout
