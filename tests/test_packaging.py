"""Packaging doctrine (#108; leptris-py#158's test_packaging pattern):
compiled packages carry the engine binary AND its source; source
packages compile it automatically on install.

The full gate (scripts/audit_artifacts.py) runs in the release
workflow against every artifact pre-upload; this checks the
INSTALLED layout when a vendored build is present (wheel installs
and source installs both vendor; dev checkouts with
YEPTRIS_LIB_PATH skip).
"""

import os

import pytest

import yeptris

PKG = os.path.dirname(yeptris.__file__)
VENDOR = os.path.join(PKG, "_vendor")
PLATFORM = os.path.join(PKG, "_platform")


_DEV_OVERRIDE = bool(os.environ.get("YEPTRIS_LIB_PATH"))
_VENDORED = os.path.isdir(VENDOR)


@pytest.mark.skipif(_DEV_OVERRIDE or not _VENDORED, reason="no vendored engine (dev build)")
class TestVendoredLayout:
    def test_engine_source_present(self):
        assert os.path.isfile(os.path.join(VENDOR, "engine", "CMakeLists.txt"))
        assert os.path.isdir(os.path.join(VENDOR, "engine", "src"))

    def test_rebuild_notes_present(self):
        assert os.path.isfile(os.path.join(VENDOR, "README.md"))

    def test_engine_binary_present(self):
        # the prebuilt engine rides _platform/<v>/ (release builds);
        # source installs land their build beside it via _build_vendored
        found = False
        if os.path.isdir(PLATFORM):
            for root, _dirs, files in os.walk(PLATFORM):
                found = found or any(
                    f.startswith("libyeptris") or f == "yeptris.dll" for f in files
                )
        found = found or any(
            os.path.isfile(os.path.join(VENDOR, n))
            for n in ("libyeptris.so", "libyeptris.dylib", "yeptris.dll", "libyeptris.dll")
        )
        assert found, "no vendored engine binary in _platform/ or _vendor/"
