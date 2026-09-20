"""Pre-upload packaging-doctrine gate (#108; leptris-py#158's
pattern): every COMPILED wheel carries the vendored engine binary
AND the engine build inputs + rebuild notes; the sdist carries the
engine source that compiles on install. The pure wheel (py3-none-any)
is exempt — it is not a compiled package. A failure fails the
publish: gates beat conventions (leptris-py's 1.9.208.0 lesson)."""

import glob
import sys
import tarfile
import zipfile

dist = sys.argv[1] if len(sys.argv) > 1 else "dist"
wheels = sorted(glob.glob(f"{dist}/*.whl"))
sdists = sorted(glob.glob(f"{dist}/*.tar.gz"))
assert wheels and sdists, f"missing artifacts: {len(wheels)} wheels, {len(sdists)} sdists"

for w in wheels:
    if w.endswith("py3-none-any.whl"):
        print(f"pure wheel (exempt): {w}")
        continue
    names = zipfile.ZipFile(w).namelist()
    assert any("_platform/" in n and n.endswith((".so", ".dll", ".dylib")) for n in names), \
        f"{w}: no vendored engine binary"
    assert any(n.endswith("_vendor/engine/CMakeLists.txt") for n in names), \
        f"{w}: no vendored engine source"
    assert any(n.endswith("_vendor/README.md") for n in names), \
        f"{w}: no vendored rebuild notes"

for s in sdists:
    names = tarfile.open(s).getnames()
    assert any(n.endswith("vendor/libyeptris/CMakeLists.txt") for n in names), \
        f"{s}: no engine source for build-on-install"

print(f"artifact doctrine OK: {len(wheels)} wheels, {len(sdists)} sdist")
