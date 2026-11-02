"""The engine-version floor: a stale libyeptris must fail AT IMPORT
naming both versions (a dev tree once loaded v0.6.7 against a
0.6.51-era package — record strides changed under it and every scalar
silently corrupted)."""

import importlib
import os
import subprocess
import sys

import pytest

from yeptris import _ffi

# Runs the REAL import seam in a fresh interpreter: ctypes.CDLL is
# monkeypatched BEFORE yeptris._ffi's first import, so the import-time
# floor gate runs against the faked engine version. A subprocess keeps
# the patched ctypes out of this process's module state.
_RELOAD_SCRIPT = """
import ctypes, sys
real_cdll = ctypes.CDLL


class _VersionFn:
    restype = None

    def __init__(self, raw):
        self._raw = raw

    def __call__(self):
        return self._raw


class _FakeLib:
    # Delegates everything to the real lib; yeptris_version faked
    # (empty version string = the symbol is absent entirely).

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        if name == "yeptris_version":
            if not sys.argv[3]:
                raise AttributeError(name)
            return _VersionFn(sys.argv[3].encode())
        return getattr(self._real, name)


ctypes.CDLL = lambda p, *a, **k: _FakeLib(real_cdll(p, *a, **k))
sys.path.insert(0, sys.argv[2])
try:
    import yeptris._ffi
except ImportError as e:
    print("IMPORTERROR:", e)
    sys.exit(0)
print("NO-RAISE: the stale engine passed the floor gate")
sys.exit(1)
"""


def _reload_with_fake_lib(raw_version: str):
    lib_path = os.environ.get("YEPTRIS_LIB_PATH", "")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=repo, YEPTRIS_LIB_PATH=lib_path)
    return subprocess.run(
        [sys.executable, "-c", _RELOAD_SCRIPT, lib_path, repo, raw_version],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_version_tuple_shapes():
    assert _ffi._version_tuple("0.6.55") == (0, 6, 55)
    assert _ffi._version_tuple("0.6.19") == (0, 6, 19)
    assert _ffi._version_tuple("0.6.55.0") == (0, 6, 55)
    assert _ffi._version_tuple("0.7") == (0, 7, 0)
    assert _ffi._version_tuple("v0.6.19") == (0, 6, 19)
    assert _ffi._version_tuple("0.6.19-beta") == (0, 6, 19)
    assert _ffi._version_tuple("garbage") == (0, 0, 0)  # fails the floor


def test_floor_accepts_at_and_above():
    _ffi.check_engine_version("0.6.19")  # exactly the floor
    _ffi.check_engine_version("0.6.20")
    _ffi.check_engine_version("0.6.56")
    _ffi.check_engine_version("0.7.0")
    _ffi.check_engine_version("1.0.0")


def test_floor_rejects_below_naming_both_versions():
    for stale in ("0.6.18", "0.6.7", "0.5.99", "garbage"):
        with pytest.raises(ImportError) as ei:
            _ffi.check_engine_version(stale)
        # both versions named: the loaded one and the floor
        assert stale in str(ei.value)
        assert "0.6.19" in str(ei.value)
        assert "record strides" in str(ei.value)


def test_loaded_engine_passes_the_floor():
    # the real import already ran the gate: the engine in this process
    # is present and at/above the floor
    assert _ffi._ENGINE_VERSION
    assert _ffi._version_tuple(_ffi._ENGINE_VERSION) >= (0, 6, 19)


def test_stale_engine_rejected_at_import(monkeypatch, tmp_path):
    # the audit incident: v0.6.7 against a 0.6.51-era package must be
    # an import-time ImportError naming BOTH versions
    r = _reload_with_fake_lib("0.6.7")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "IMPORTERROR" in r.stdout
    assert "0.6.7" in r.stdout
    assert "0.6.19" in r.stdout
    assert "record strides" in r.stdout


def test_engine_below_floor_by_one_patch_rejected(monkeypatch, tmp_path):
    r = _reload_with_fake_lib("0.6.18")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "IMPORTERROR" in r.stdout


def test_unversioned_engine_fails_closed(monkeypatch, tmp_path):
    # yeptris_version exists since the C repo's initial commit, so a
    # library WITHOUT the symbol predates the floor entirely: the gate
    # must not silently pass an engine whose version it cannot read
    r = _reload_with_fake_lib("")  # "" -> the fake omits the symbol
    assert r.returncode == 0, r.stdout + r.stderr
    assert "IMPORTERROR" in r.stdout
    assert "0.6.19" in r.stdout
