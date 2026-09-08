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

## Performance

`python3 bench.py` — same-process comparison against PyYAML (pure)
and CSafeLoader/CDumper (libyaml C extensions):

- **load**: 28-39x PyYAML pure, 3.8-6.1x CSafeLoader
- **dump**: ~1.7x PyYAML pure, ~2x behind CDumper (which walks the
  tree entirely in C — a no-C-extension design's ceiling is the
  Python walk itself; the tree raises through ONE
  `yeptris_document_build` call)

Both directions are O(chunks) in FFI calls: loads drain records in
two calls, dumps build through one flat entry array.

## JSON (strict RFC 8259) — `yeptris.json`

`yeptris.json.loads` is json.loads-compatible (values, rejects,
types, `json.JSONDecodeError` — including stdlib's default NaN/
Infinity constant quirk) with two engines behind one contract:

- **native** — the opt-in extension `ext/yeptris_native.c`: a fused
  scan-kernel descent over libyeptris (the same shape as the Ruby
  binding's materializer), with a 1024-slot interned-free key cache.
  Build it with `YEPTRIS_LIB_PATH` + `YEPTRIS_SRC` set
  (`python3 setup.py build_ext --inplace`); CI builds and gates it.
- **strict-ffi** — the default pure path: `yeptris_parse_json` is
  the validator gate, then the columnar value walk converts under
  JSON rules. No C extension, correct everywhere.

`benchmark/json_profile.py` is the referee (order-alternating
interleave vs the C scanner): the native engine runs **0.67-0.87x
json.loads mean (83-85% head-to-head)** on the 144 KB reference
corpus; shape decomposition: int arrays 0.63x, float arrays 0.77x,
long strings 0.85x, unique-key maps 0.95x. The previous pure walk
was ~15x BEHIND — the fused materializer is the entire gap.
