#!/bin/bash
# The riscv64 wheel leg's container payload (release.yml wheel-riscv64;
# the release workflow cannot be dispatched on a branch without
# attaching wheels to a released tag, so this script is ALSO what the
# wheel-riscv64-validation.yml workflow runs against arbitrary refs).
#
# Runs INSIDE `docker run --platform linux/riscv64` on a manylinux
# image (the musl-leg idiom, emulated by QEMU on the x86_64 runner):
# builds the C engine at $C_TAG, grafts its source, builds the vendored
# abi3 wheel, auditwheel-repairs it, then runs the FULL pytest suite
# AND scripts/wheel_smoke.py against the INSTALLED wheel inside the
# same container (leptris/leptris-py#346's "full suite under qemu"
# guarantee — an emulated leg that only imports is not validated).
#
# Layout expectations (the release workflow's staging):
#   /work/yeptris-py   — this repository's tree (staged, no .git)
#   /work/dist         — repaired wheels land here for the attach step
#   $C_TAG             — the lockstep C core tag (e.g. v0.6.63)
#   $PKG_TAG           — the package tag (grafted-source pin notes)
set -euo pipefail

WORK=/work
PKG="$WORK/yeptris-py"
C_SRC="$WORK/yeptris-c"

echo "== riscv64 leg: manylinux $(cat /etc/os-release | grep -E '^PRETTY' ) =="
uname -m

# The manylinux images carry the interpreters under /opt/python/<tag>/
# bin — build with the abi3 FLOOR interpreter (cp39: the version the
# wheel's limited-api tag claims; free-threaded *t builds are not what
# the wheel targets). The riscv64 flavor keeps the full set.
PYBIN="$(ls -d /opt/python/cp39-cp39 2>/dev/null || true)"
if [ -z "$PYBIN" ]; then
  PYBIN="$(ls -d /opt/python/cp3*-cp3[0-9]* 2>/dev/null | grep -v 't$' | sort -V | tail -1 || true)"
fi
PYTHON="$(command -v python3 || true)"
if [ -n "$PYBIN" ] && [ -x "$PYBIN/bin/python3" ]; then
  PYTHON="$PYBIN/bin/python3"
fi
[ -n "$PYTHON" ] || { echo "no python interpreter found" >&2; exit 1; }
export PATH="$(dirname "$PYTHON"):$PATH"
echo "python: $($PYTHON -VV)"

# cmake: recent manylinux images ship it; install only if missing
# (Ubuntu-based images — the glibc flavors — use apt; yum kept for any
# CentOS-based flavor). No ninja here — the default Make generator
# builds the engine.
if ! command -v cmake >/dev/null; then
  if command -v apt-get >/dev/null; then
    apt-get update -qq && apt-get install -y -qq --no-install-recommends cmake >/dev/null
  elif command -v yum >/dev/null; then
    yum install -y cmake >/dev/null
  fi
fi

# The C core, at the lockstep tag (the CI-on-released-tag law)
git config --global --add safe.directory "$WORK" || true
git clone --quiet --depth 1 --branch "$C_TAG" https://github.com/leptris/yeptris "$C_SRC"

cmake -B "$C_SRC/build" -S "$C_SRC" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
  -DYEPTRIS_BUILD_CLI=OFF -DYEPTRIS_BUILD_SHARED=ON
cmake --build "$C_SRC/build"

cd "$PKG"
bash scripts/graft_engine_source.sh "$C_SRC"

LIB="$C_SRC/build/src/libyeptris.so"
python3 -m pip install -q setuptools wheel build auditwheel
YEPTRIS_VENDOR=1 YEPTRIS_LIB_PATH="$LIB" \
  YEPTRIS_SRC="$C_SRC/src" \
  python3 setup.py bdist_wheel --py-limited-api=cp39

mkdir -p "$WORK/dist/final"
# auditwheel repair pins the platform tag AND proves the only external
# symbols are within the manylinux_2_39 policy (the riscv64 images
# start at 2_39 — no lower glibc flavor exists yet)
auditwheel repair dist/*.whl -w "$WORK/dist/final"
ls -la "$WORK/dist/final"

# The full battery against the INSTALLED wheel: a clean venv with only
# the wheel + the test deps. The suite runs OUTSIDE the package tree —
# conftest.py's sys.path probe would otherwise shadow the wheel with
# the source tree (which has no _native extension of its own).
python3 -m venv "$WORK/venv"
# cp39's ensurepip bundles pip 23.0.1, whose vendored packaging predates
# riscv64 manylinux support (it only offers linux_riscv64) — upgrade
# pip FIRST or the repaired manylinux_*_riscv64 wheel is rejected as
# "not a supported wheel on this platform"
"$WORK/venv/bin/pip" install -q --upgrade pip
"$WORK/venv/bin/pip" install -q --no-index "$WORK"/dist/final/*.whl
"$WORK/venv/bin/pip" install -q pytest pyyaml

mkdir -p "$WORK/suite"
cp -R "$PKG/tests" "$PKG/scripts" "$PKG/README.md" "$WORK/suite/"
cd "$WORK/suite"
"$WORK/venv/bin/python" -m pytest tests -q

# The release smoke battery, no env overrides (the vendored-lib proof)
cd "$WORK"
env -u YEPTRIS_LIB_PATH -u YEPTRIS_SRC \
  "$WORK/venv/bin/python" "$PKG/scripts/wheel_smoke.py"

echo "RISCV64 LEG PASS"
