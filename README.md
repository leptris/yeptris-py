# yeptris — YAML for Python at libleptris speed

An FFI-based (no C extension) YAML library over
[libyeptris](https://github.com/leptris/yeptris) — the YAML
counterpart of libleptris. PyYAML-compatible semantics, one shared
library, zero compilation at install.

Part of the yeptris program: *the best (multiple dimensions)
YAML/JSON parser/builder ever* — conformance, performance,
safety/hardening, API surfaces, transport correctness, and trust,
held simultaneously, in C, Ruby, Rust, and JS. This binding carries
the PyYAML-compatible surface plus CBOR, tracking every libyeptris
release in lockstep. The achievement program lives in the engine's
PLAN.md ("The best-ever program").

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

yaml.safe_load_all("--- 1\n--- two\n")   # lazy generator: [1, 'two']
yaml.safe_dump({"b": 2, "a": [1, "x"]})  # 'a:\n  - 1\n  - x\nb: 2\n'
yaml.safe_dump_all([{"a": 1}, {"b": 2}]) # 'a: 1\n---\nb: 2\n'

yeptris.load("k: v")        # the neutral surface
yeptris.dump({"k": [1, 2]})
yeptris.load_all("--- 1\n--- 2\n")  # every document, as a list
yeptris.dump_all([{"k": 1}, [2]])   # the '---'-separated stream
```

Typing follows PyYAML's SafeLoader (YAML 1.1 implicit typing):
`yes/no/on/off` booleans, `0x`/leading-0/`0b`/sexagesimal integers,
dot-required floats, timestamps with offsets, merge keys, anchor
identity. Every deliberate divergence is pinned by a test.

### Validation

The validate face (libyeptris >= 0.6.59): JSON Schema draft 2020-12
and the YAML schema-family walk over one engine pass — the WHOLE
report every time (diagnostics enumerate every miss, never
first-fail), each a `Diagnostic` dataclass (path segments, keyword,
message, 1-based line/col):

```python
# needs libyeptris >= 0.6.59
from yeptris import yaml

schema = '{"type": "object", "required": ["name"]}'
yaml.validate(b"name: yeptris\n", json_schema=schema)  # []
yaml.is_valid(b"age: 1\n", json_schema=schema)         # False

for d in yaml.validate(b"age: 1\n", json_schema=schema):
    print(d.keyword, d.pointer, d.message, d.line, d.col)
    # required /name missing required property "name" 1 1

s = yaml.compile_schema(schema)  # parse ONCE, validate many
s.is_valid(b"name: x\n")         # True

yaml.safe_load(b"name: x\n", json_schema=schema)  # {'name': 'x'}
try:
    yaml.safe_load(b"age: 1\n", json_schema=schema)  # raises
except yaml.ValidationError as e:
    print(e.diagnostics[0].keyword)  # required
yaml.safe_load(b"a: 1\na: 2\n", validate=True, collect=True)
# ({'a': 2}, [Diagnostic(... duplicate-key ...)]) — the family walk
```

Without `json_schema=`, `validate(data)` runs the schema-family walk:
explicit-tag consistency (spec 10.3) and mapping-key uniqueness by
canonical form (`0o13` and `0xB` are the same key, spec 3.2.1.3);
`schema=` selects `SCHEMA_12_JSON` for the spec 10.2 JSON
productions. A $ref that resolves nowhere is a diagnostic, not an
exception; a stream that does not parse raises the usual load-stage
error. Older engines raise `YeptrisError` (`yeptris.yaml` ships the
feature detection).

The multi-document stream face is PyYAML's law: `load_all`/
`safe_load_all` are lazy generators (nothing parses until iteration;
a bad document raises there), empty documents yield `None`, a
zero-document stream yields nothing, and `safe_load` answers the
first document only — silently, the binding's documented divergence
(PyYAML raises on multi-document input). One pinned divergence
remains: the engine parses a stream in one bulk drain, so an invalid
stream raises at the first iteration with no documents yielded
(PyYAML's per-document parser yields the good prefix first).

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

## Installing the native JSON engine (platform wheels)

`pip install yeptris` on macOS (arm64) and manylinux (x86_64)
installs **platform wheels** that vendor libyeptris and the native
JSON materializer (abi3 — one wheel per platform serves CPython
3.9+): zero compiler, zero env vars, `yeptris.json.engine()` is
`native` out of the box. Every wheel is release-smoked in a clean
venv before publish. Other platforms install the pure package (the
strict-ffi JSON engine + YAML via libyeptris through the
`YEPTRIS_LIB_PATH` ladder); building the extension from source is
`YEPTRIS_LIB_PATH=... YEPTRIS_SRC=... python3 setup.py build_ext
--inplace`.
