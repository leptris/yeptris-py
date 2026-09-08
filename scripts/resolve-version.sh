#!/bin/sh
# The lockstep coordinates for release jobs: the package version
# (pyproject is the SSOT) and the matching C core tag (first three
# components). Single source, no inline-python quoting hazards.
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
V="$(sed -nE 's/^version = "([0-9.]+)"/\1/p' "$root/pyproject.toml" | head -1)"
[ -n "$V" ] || { echo "version not found in pyproject.toml" >&2; exit 1; }
C="$(echo "$V" | cut -d. -f1-3)"
echo "PKG_VERSION=$V"
echo "C_TAG=v$C"
echo "PKG_TAG=v$V"
