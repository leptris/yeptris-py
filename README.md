# yeptris — YAML for Python at libleptris speed

An FFI-based (no C extension) YAML library over
[libyeptris](https://github.com/leptris/yeptris) — the YAML
counterpart of libleptris. PyYAML-compatible semantics, one shared
library, zero compilation at install.

## Install (development)

```sh
# the sibling C checkout: ~/src/leptris/yeptris
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DYEPTRIS_BUILD_SHARED=ON
cmake --build build

cd ~/src/leptris/yeptris-py
YEPTRIS_LIB_PATH=../yeptris/build/src/libyeptris.dylib python3 -m pytest
```

Without `YEPTRIS_LIB_PATH` the loader falls back to a vendored
`yeptris/_platform/<tag>/` copy, then to the sibling checkout's
build directory. Any `libyeptris.{so,dylib,dll}` path works.

## Usage

```python
import yeptris
from yeptris import yaml  # PyYAML-compatible surface

yaml.safe_load("name: yeptris\nrating: 10\n")
# {'name': 'yeptris', 'rating': 10}

yaml.safe_load_all("--- 1\n--- two\n")   # [1, 'two']
yaml.safe_dump({"b": 2, "a": [1, "x"]})  # 'a:\n  - 1\n  - x\nb: 2\n'

yeptris.load("k: v")        # the neutral surface
yeptris.dump({"k": [1, 2]})
```

Typing follows PyYAML's SafeLoader (YAML 1.1 implicit typing):
`yes/no/on/off` booleans, `0x`/leading-0/`0b`/sexagesimal integers,
dot-required floats, timestamps with offsets, merge keys, anchor
identity. Every deliberate divergence is pinned by a test.

## Design

One parse, one bulk drain: the record array and string arena are
read in two FFI calls, then a pure-Python walk over the unpacked
records — the FFI tax is O(1) per document, never per event (the
same seam yeptris-ruby rides). The 36-byte record layout is
ABI-pinned in the C header and mirrored in `_ffi.py`.

## Benchmarks

`python3 bench.py` — same-process comparison against PyYAML (pure)
and CSafeLoader (libyaml). On the dev box: 17-20x PyYAML pure,
1.9-2.5x CSafeLoader on scalar/block shapes.
