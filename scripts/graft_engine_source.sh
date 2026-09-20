#!/usr/bin/env bash
# Packaging doctrine (#108, leptris-py#158's pattern): compiled
# packages carry the engine binary AND its source. Grafts the pinned
# libyeptris build inputs into yeptris/_vendor/ beside the _platform
# binaries, with the pinned version and the exact rebuild command.
# Usage: graft_engine_source.sh <libyeptris checkout> (PKG_TAG pins
# the version the README reports; the release workflow sets it).
set -euo pipefail
SRC="${1:?usage: graft_engine_source.sh <libyeptris checkout>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/yeptris/_vendor"
VER="${PKG_TAG:-unpinned}"

rm -rf "$VENDOR/engine"
mkdir -p "$VENDOR/engine"
cp "$SRC/CMakeLists.txt" "$VENDOR/engine/"
cp "$SRC"/LICENSE* "$VENDOR/engine/" 2>/dev/null || true
cp -R "$SRC/cmake" "$VENDOR/engine/cmake"
cp -R "$SRC/src" "$VENDOR/engine/src"
find "$VENDOR/engine/src" -name build -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$VENDOR/README.md" << VEOF
libyeptris ${VER} is vendored here.

- _platform/<v>/ — the prebuilt engine this package runs on
- engine/ — the exact source it was built from; rebuild with:

      cmake -B build -S engine \
        -DCMAKE_BUILD_TYPE=Release \
        -DYEPTRIS_BUILD_TESTING=OFF -DYEPTRIS_BUILD_CLI=OFF \
        -DYEPTRIS_BUILD_BENCHMARKS=OFF -DYEPTRIS_BUILD_SHARED=ON
      cmake --build build

Source installs (the sdist) compile vendor/libyeptris automatically
via setup.py; YEPTRIS_LIB_PATH overrides the engine path.
VEOF
echo "grafted engine source: $VENDOR/engine (libyeptris ${VER})"
