"""ctypes bindings to libyeptris — one signature, declared once.

No C extension, no compile step: the shared library is dlopen'd the
way the FFI gem does it for yeptris-ruby. Search order:
  1. $YEPTRIS_LIB_PATH
  2. a vendored yeptris/_platform/<tag>/libyeptris.* next to the package
  3. a sibling C checkout's build directory (development)

The event record layout (36 bytes) is ABI-pinned in the C header and
mirrored here; `_RECORD` unpacks one record in a single call.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from pathlib import Path

# YeptrisStatus (error.h)
OK = 0
ERROR_PARSE = 1
ERROR_MEMORY = 2
ERROR_DEPTH = 3
ERROR_ENCODING = 4
ERROR_IO = 5
ERROR_ARG = 6
ERROR_UNSUPPORTED = 7
ERROR_INTERNAL = 8

# YeptrisSchema (resolve.h)
SCHEMA_12_CORE = 0
SCHEMA_11_COMPAT = 1

# YeptrisEventType (events.h)
STREAM_START = 1
STREAM_END = 2
DOCUMENT_START = 3
DOCUMENT_END = 4
SEQUENCE_START = 5
SEQUENCE_END = 6
MAPPING_START = 7
MAPPING_END = 8
SCALAR = 9
ALIAS = 10

# YeptrisTagId (resolve.h)
TAG_STR = 0
TAG_INT = 1
TAG_FLOAT = 2
TAG_BOOL = 3
TAG_NULL = 4
TAG_TIMESTAMP = 5
TAG_SEQ = 6
TAG_MAP = 7
TAG_BINARY = 8
TAG_MERGE = 9
TAG_VALUE = 10

# YeptrisScalarStyle (dom.h)
STYLE_PLAIN = 1
STYLE_SINGLE_QUOTED = 2
STYLE_DOUBLE_QUOTED = 3
STYLE_LITERAL = 4
STYLE_FOLDED = 5

# YeptrisEventRecord: type, style, flags, tag_id (uint8 x4) then
# line, col, value_off, value_len, anchor_off, anchor_len, tag_off,
# tag_len (uint32 x8). sizeof == 36, ABI-pinned.
_RECORD = struct.Struct("<4B8I")
RECORD_SIZE = _RECORD.size
assert RECORD_SIZE == 36

# Flag bits (events.h)
EF_FLOW = 1 << 0
EF_EXPLICIT = 1 << 1
EF_IMPLICIT = 1 << 2


class YeptrisError(Exception):
    """Base: something went wrong inside or around the library."""


class ParseError(YeptrisError):
    """Malformed YAML. Carries the 1-based line and column."""

    def __init__(self, message: str, line: int = 0, column: int = 0):
        super().__init__(f"{message} at line {line}, column {column}"
                         if line else message)
        self.line = line
        self.column = column


def _candidate_paths():
    env = os.environ.get("YEPTRIS_LIB_PATH")
    if env:
        yield Path(env)
    here = Path(__file__).resolve().parent
    for p in sorted(here.glob("_platform/*/libyeptris.*")):
        yield p
    names = ["libyeptris.dylib", "libyeptris.so"]
    for build in ("build", "build-validate"):
        for name in names:
            yield here.parent.parent / "yeptris" / build / "src" / name


def _load_lib() -> ctypes.CDLL:
    tried = []
    for path in _candidate_paths():
        try:
            if path.exists():
                return ctypes.CDLL(str(path))
            tried.append(str(path))
        except OSError:
            tried.append(str(path))
    raise YeptrisError(
        "could not load the libyeptris library. Set YEPTRIS_LIB_PATH to a "
        "libyeptris.{so,dylib,dll}, install a platform wheel, or build the "
        "sibling C checkout. Tried: " + ", ".join(tried)
    )


_lib = _load_lib()

_u8p = ctypes.POINTER(ctypes.c_char)
_p = ctypes.c_void_p
_sz = ctypes.c_size_t

_lib.yeptris_recorder_new_ex.argtypes = [ctypes.c_int]
_lib.yeptris_recorder_new_ex.restype = _p
_lib.yeptris_recorder_feed.argtypes = [_p, ctypes.c_char_p, _sz, ctypes.c_int]
_lib.yeptris_recorder_feed.restype = ctypes.c_int
_lib.yeptris_recorder_records.argtypes = [_p, ctypes.POINTER(_sz)]
_lib.yeptris_recorder_records.restype = _u8p
_lib.yeptris_recorder_arena.argtypes = [_p, ctypes.POINTER(_sz)]
_lib.yeptris_recorder_arena.restype = ctypes.c_char_p
_lib.yeptris_recorder_free.argtypes = [_p]

_lib.yeptris_last_error.argtypes = [ctypes.POINTER(ctypes.c_uint32),
                                    ctypes.POINTER(ctypes.c_uint32)]
_lib.yeptris_last_error.restype = ctypes.c_char_p

_lib.yeptris_document_new.argtypes = []
_lib.yeptris_document_new.restype = _p
_lib.yeptris_document_free.argtypes = [_p]
_lib.yeptris_document_set_root.argtypes = [_p, _p]
_lib.yeptris_document_set_root.restype = ctypes.c_int
_lib.yeptris_node_new_scalar.argtypes = [_p, ctypes.c_char_p, _sz, ctypes.c_int]
_lib.yeptris_node_new_scalar.restype = _p
_lib.yeptris_node_new_sequence.argtypes = [_p]
_lib.yeptris_node_new_sequence.restype = _p
_lib.yeptris_node_new_mapping.argtypes = [_p]
_lib.yeptris_node_new_mapping.restype = _p
_lib.yeptris_node_seq_add.argtypes = [_p, _p]
_lib.yeptris_node_seq_add.restype = ctypes.c_int
_lib.yeptris_node_map_add.argtypes = [_p, ctypes.c_char_p, _sz, _p]
_lib.yeptris_node_map_add.restype = ctypes.c_int
_lib.yeptris_node_map_add_node.argtypes = [_p, _p, _p]
_lib.yeptris_node_map_add_node.restype = ctypes.c_int
_lib.yeptris_document_build.argtypes = [_p, ctypes.c_void_p, _sz,
                                        ctypes.c_char_p, _sz]
_lib.yeptris_document_build.restype = ctypes.c_int

# YeptrisBuildEntry: op, style, reserved, off, len — 12 bytes, pinned
BUILD_ENTRY = struct.Struct("<BBHII")
BUILD_SCALAR, BUILD_SEQ, BUILD_MAP, BUILD_END = 1, 2, 3, 4

_lib.yeptris_serialize.argtypes = [_p, ctypes.POINTER(_sz)]
_lib.yeptris_serialize.restype = ctypes.c_char_p

# serialize() returns a malloc'd buffer (caller frees, emit.h) — the
# library allocates with the system allocator, so libc free is exact
libc_free = ctypes.CDLL(None).free
libc_free.argtypes = [ctypes.c_void_p]


def last_error():
    line = ctypes.c_uint32(0)
    col = ctypes.c_uint32(0)
    msg = _lib.yeptris_last_error(ctypes.byref(line), ctypes.byref(col))
    return (msg.decode("utf-8", "replace") if msg else "parse error",
            line.value, col.value)


def free_buffer(buf) -> None:
    if buf:
        libc_free(ctypes.cast(buf, ctypes.c_void_p))


def drain(yaml: bytes, schema: int):
    """One parse + one bulk read: the flat record array and the arena.

    Returns (records_bytes, arena_bytes) — the FFI tax is O(1) per
    parse, never per event (the same seam yeptris-ruby rides).
    """
    rec = _lib.yeptris_recorder_new_ex(schema)
    if not rec:
        raise YeptrisError("recorder allocation failed")
    try:
        st = _lib.yeptris_recorder_feed(rec, yaml, len(yaml), 1)
        if st != OK:
            msg, line, col = last_error()
            raise ParseError(msg, line, col)
        n = ctypes.c_size_t(0)
        raw = _lib.yeptris_recorder_records(rec, ctypes.byref(n))
        records = ctypes.string_at(raw, n.value * RECORD_SIZE) if n.value else b""
        alen = ctypes.c_size_t(0)
        arena_p = _lib.yeptris_recorder_arena(rec, ctypes.byref(alen))
        arena = arena_p[:alen.value] if alen.value else b""
        return records, arena
    finally:
        _lib.yeptris_recorder_free(rec)
