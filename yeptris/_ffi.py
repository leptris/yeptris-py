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

import array
import ctypes
import os
import struct
import sys
from pathlib import Path

# The exception taxonomy is defined ONCE in _errors (a leaf module:
# importing it from the yaml subpackage from here would cycle the
# package import). Re-exported so `F.ParseError` keeps working.
from ._errors import (  # noqa: F401 — re-exports
    ComposerError,
    ConstructorError,
    EmitterError,
    MarkedYAMLError,
    ParserError,
    ReaderError,
    RepresenterError,
    ScannerError,
    YAMLError,
    ParseError,
    YeptrisError,
)

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
ERROR_SCHEMA = 9  # validation: the report carries diagnostics (#882)

# YeptrisSchema (resolve.h)
SCHEMA_12_CORE = 0
SCHEMA_11_COMPAT = 1
SCHEMA_12_JSON = 2  # spec 10.2: JSON productions; plain scalars matching
# none are errors (#882)

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
BUILD_TAG = 5  # apply an explicit tag to the last-placed entry (#300)
STYLE_FOLDED = 5

# YeptrisEventRecord: type, style, flags, tag_id (uint8 x4) then
# line, col, end_line, end_col, value_off, value_len, anchor_off,
# anchor_len, tag_off, tag_len (uint32 x10). sizeof == 44 — the
# #179 end marks added in the C 0.6.19 window (ABI-pinned).
_RECORD = struct.Struct("<4B10I")
RECORD_SIZE = _RECORD.size
assert RECORD_SIZE == 44

# Flag bits (events.h)
EF_FLOW = 1 << 0
EF_EXPLICIT = 1 << 1
EF_IMPLICIT = 1 << 2


# --- engine-version floor (import gate) --------------------------------
# The event-record layout is ABI-pinned to the C 0.6.19 window (44-byte
# records, the #179 end marks). A dev tree once loaded v0.6.7 against a
# 0.6.51-era package: record strides changed under it and every scalar
# silently corrupted. Fail AT IMPORT, naming both versions.

ENGINE_VERSION_FLOOR = (0, 6, 19)


def _version_tuple(text: str) -> tuple:
    """'0.6.55' -> (0, 6, 55); tolerates decoration ('v0.6.55.0',
    '0.6.19-beta') and stops at the first part carrying no digits
    (garbage -> (0, 0, 0), which fails the floor — an unreadable
    version is a stale engine)."""
    parts = []
    for part in text.strip().split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            elif digits:
                break  # suffix after the number ('19-beta')
        if not digits:
            break
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_engine_version(version: str, floor: tuple = ENGINE_VERSION_FLOOR) -> None:
    """Raise ImportError when the loaded engine is below the pinned
    floor, naming BOTH versions (the loaded one and the floor)."""
    if _version_tuple(version) < tuple(floor):
        raise ImportError(
            "libyeptris %s is too old for this yeptris package: the "
            "pinned event-record strides require libyeptris >= %s "
            "(set YEPTRIS_LIB_PATH to a newer build)"
            % (version, ".".join(str(p) for p in floor))
        )


def _candidate_paths():
    env = os.environ.get("YEPTRIS_LIB_PATH")
    if env:
        yield Path(env)
    here = Path(__file__).resolve().parent
    for p in sorted(here.glob("_platform/*/libyeptris.*")):
        yield p
    names = ["libyeptris.dylib", "libyeptris.so", "libyeptris.dll"]
    for build in ("build", "build-validate"):
        for name in names:
            yield here.parent.parent / "yeptris" / build / "src" / name


def _load_lib() -> ctypes.CDLL:
    tried = []
    for path in _candidate_paths():
        try:
            if path.exists():
                lib = ctypes.CDLL(str(path))
                # Windows vendors the DLL under BOTH names (the ctypes
                # glob prefers libyeptris.dll); load the sibling too so
                # the loader registers the module under whichever name
                # _native.pyd's import table references
                sibling = path.with_name(
                    "yeptris.dll" if path.name == "libyeptris.dll" else "libyeptris.dll"
                )
                if sibling != path and sibling.exists():
                    try:
                        ctypes.CDLL(str(sibling))
                    except OSError:
                        pass
                return lib
            tried.append(str(path))
        except OSError:
            tried.append(str(path))
    raise YeptrisError(
        "could not load the libyeptris library. Set YEPTRIS_LIB_PATH to a "
        "libyeptris.{so,dylib,dll}, install a platform wheel, or build the "
        "sibling C checkout. Tried: " + ", ".join(tried)
    )


_lib = _load_lib()

try:  # yeptris_version exists since the C repo's initial commit, so a
    # library WITHOUT the symbol predates the floor entirely — fail closed
    _lib.yeptris_version.restype = ctypes.c_char_p
    _raw_version = _lib.yeptris_version()
except AttributeError:
    _raw_version = None
if not _raw_version:
    raise ImportError(
        "could not determine the libyeptris version (the library "
        "carries no yeptris_version symbol, or it returned NULL): "
        "this yeptris package requires libyeptris >= %s"
        % ".".join(str(p) for p in ENGINE_VERSION_FLOOR)
    )
_ENGINE_VERSION = _raw_version.decode("ascii", "replace")
check_engine_version(_ENGINE_VERSION)
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
_lib.yeptris_recorder_arena.restype = _u8p  # the arena is NOT NUL-terminated:
# it carries escaped NULs and binary scalars -- a c_char_p restype makes
# ctypes truncate the copy at the first NUL (every later scalar
# materializes wrong) or scan past the buffer when there is none
_lib.yeptris_recorder_free.argtypes = [_p]

_lib.yeptris_last_error.argtypes = [ctypes.POINTER(ctypes.c_uint32),
                                    ctypes.POINTER(ctypes.c_uint32)]
_lib.yeptris_last_error.restype = ctypes.c_char_p

_lib.yeptris_document_new.argtypes = []
_lib.yeptris_document_new.restype = _p
_lib.yeptris_document_new_schema.argtypes = [ctypes.c_int]
_lib.yeptris_document_new_schema.restype = _p
_lib.yeptris_document_free.argtypes = [_p]
_lib.yeptris_parse_json.argtypes = [ctypes.c_char_p, _sz, ctypes.POINTER(ctypes.c_int)]
_lib.yeptris_parse_json.restype = _p

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

# restype c_void_p: the malloc'd buffer's pointer must stay a pointer
# so it can be freed (c_char_p would auto-convert and leak it)
_lib.yeptris_serialize.argtypes = [_p, ctypes.POINTER(_sz)]
_lib.yeptris_serialize.restype = ctypes.c_void_p

# JSON serialization (json.h): absent on older vendored libraries —
# yeptris.json feature-detects
try:
    _lib.yeptris_serialize_json.argtypes = [_p, ctypes.POINTER(_sz)]
    _lib.yeptris_serialize_json.restype = ctypes.c_void_p
    _lib.yeptris_serialize_json_ex.argtypes = [_p, ctypes.POINTER(_sz), ctypes.c_int]
    _lib.yeptris_serialize_json_ex.restype = ctypes.c_void_p
    JSON_SERIALIZE_AVAILABLE = True
except AttributeError:
    JSON_SERIALIZE_AVAILABLE = False

# CBOR (RFC 8949; TODO.cbor): absent on older vendored libraries —
# the yeptris.cbor module feature-detects
try:
    _lib.yeptris_cbor_decode.argtypes = [_p, _sz, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int)]
    _lib.yeptris_cbor_decode.restype = ctypes.c_void_p
    _CBOR_ITEM_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)
    _lib.yeptris_cbor_decode_sequence.argtypes = [
        _p, _sz, ctypes.c_uint32, _CBOR_ITEM_CB, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    _lib.yeptris_cbor_decode_sequence.restype = ctypes.c_size_t
    _lib.yeptris_cbor_encode.argtypes = [_p, ctypes.c_uint32, ctypes.POINTER(_sz)]
    _lib.yeptris_cbor_encode.restype = ctypes.c_void_p
    _lib.yeptris_cbor_encode_sequence.argtypes = [
        ctypes.POINTER(_p), ctypes.c_size_t, ctypes.c_uint32, ctypes.POINTER(_sz)]
    _lib.yeptris_cbor_encode_sequence.restype = ctypes.c_void_p
    CBOR_AVAILABLE = True
except AttributeError:
    CBOR_AVAILABLE = False

# Emit options (emit.h 13B): versioned by size — future fields stay
# optional. best_width drives the width-aware flow folding; a
# non-NULL struct with canonical=0 is byte-identical to the NULL-opts
# fidelity route (the emitter's only width consumer is flow wrapping).
class _EmitOptions(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("canonical", ctypes.c_int),
                ("best_width", ctypes.c_int),
                ("explicit_doc_start", ctypes.c_int)]


try:
    _lib.yeptris_serialize_ex.argtypes = [_p, ctypes.POINTER(_EmitOptions),
                                          ctypes.POINTER(_sz)]
    _lib.yeptris_serialize_ex.restype = ctypes.c_void_p
    EMIT_OPTIONS_AVAILABLE = True
except AttributeError:
    EMIT_OPTIONS_AVAILABLE = False

# The validation face (validate.h, #882): absent on vendored libraries
# predating the C 0.6.59 compiled-schema economy — yeptris.yaml
# feature-detects (the JSON/CBOR pattern).
class _Diagnostic(ctypes.Structure):
    # uint32 code/line/col/offset, then the bounded NUL-terminated
    # path (256) and detail (128) texts — 400 bytes, ABI-pinned (the
    # C test_abi carries the same static assert)
    _fields_ = [("code", ctypes.c_uint32), ("line", ctypes.c_uint32),
                ("col", ctypes.c_uint32), ("offset", ctypes.c_uint32),
                ("path", ctypes.c_char * 256),
                ("detail", ctypes.c_char * 128)]


assert ctypes.sizeof(_Diagnostic) == 16 + 256 + 128

try:
    _lib.yeptris_validate.argtypes = [ctypes.c_char_p, _sz, ctypes.c_int,
                                      ctypes.POINTER(_p)]
    _lib.yeptris_validate.restype = ctypes.c_int
    _lib.yeptris_json_schema_validate.argtypes = [ctypes.c_char_p, _sz,
                                                  ctypes.c_char_p, _sz,
                                                  ctypes.POINTER(_p)]
    _lib.yeptris_json_schema_validate.restype = ctypes.c_int
    _lib.yeptris_schema_compile.argtypes = [ctypes.c_char_p, _sz]
    _lib.yeptris_schema_compile.restype = _p
    _lib.yeptris_schema_validate.argtypes = [_p, ctypes.c_char_p, _sz,
                                             ctypes.POINTER(_p)]
    _lib.yeptris_schema_validate.restype = ctypes.c_int
    _lib.yeptris_schema_free.argtypes = [_p]
    _lib.yeptris_validate_report_count.argtypes = [_p]
    _lib.yeptris_validate_report_count.restype = _sz
    # borrowed from the report — valid until validate_report_free
    _lib.yeptris_validate_report_at.argtypes = [_p, _sz]
    _lib.yeptris_validate_report_at.restype = _u8p
    _lib.yeptris_validate_report_free.argtypes = [_p]
    VALIDATE_AVAILABLE = True
except AttributeError:
    VALIDATE_AVAILABLE = False

# serialize() returns a malloc'd buffer (caller frees, emit.h) — the
# library allocates with the system allocator, so libc free is exact.
# POSIX: CDLL(None) searches the process symbol table (falls through
# to libc). Windows has no process fallback — free lives in
# ucrtbase.dll, the same CRT the MSVC-built DLL allocates from.
# Prefer the DLL's own allocator-matching free (yeptris_free,
# libyeptris >= 0.6.6): Windows has no process-symbol fallback for
# ctypes, and CDLL(None).free resolves only on POSIX. Older sources
# fall back to the libc/ucrtbase chain.
try:
    libc_free = _lib.yeptris_free
except AttributeError:
    try:
        libc_free = ctypes.CDLL(None).free
    except (OSError, AttributeError):
        libc_free = ctypes.CDLL("ucrtbase.dll").free
libc_free.argtypes = [ctypes.c_void_p]

# The tolerant per-document stream iterator (parse.h, engine #921) and
# its framing accessors (slice + buffer, the engine's slice release):
# hosts carrying their own value route re-parse each frame's bytes
# through the drain — one typing SSOT, zero DOM walk. Feature-detected
# (the validate pattern): each_document imports only on engines with
# BOTH symbol sets; the binding faces skip on older vendored libraries.
class _ParseOptions(ctypes.Structure):
    # parse.h YeptrisParseOptions: ABI-frozen field order (the Ruby
    # binding carries the same layout)
    _fields_ = [("schema", ctypes.c_int), ("max_depth", ctypes.c_int),
                ("strict", ctypes.c_int), ("tab_policy", ctypes.c_int),
                ("recover", ctypes.c_int)]


STREAM_ITER_AVAILABLE = False
try:
    _lib.yeptris_stream_iter_new.argtypes = [ctypes.c_char_p, _sz,
                                             ctypes.POINTER(_ParseOptions)]
    _lib.yeptris_stream_iter_new.restype = _p
    _lib.yeptris_stream_iter_next.argtypes = [_p, ctypes.POINTER(_p),
                                              ctypes.POINTER(_sz)]
    _lib.yeptris_stream_iter_next.restype = ctypes.c_int
    _lib.yeptris_stream_iter_status.argtypes = [
        _p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    _lib.yeptris_stream_iter_status.restype = ctypes.c_int
    _lib.yeptris_stream_iter_free.argtypes = [_p]
    STREAM_ITER_AVAILABLE = True
except AttributeError:
    pass

# POINTER(c_char), not c_char_p: the buffer out-param must stay an
# address (a c_char_p restype hands back an immutable bytes snapshot —
# there would be nothing to offset the slice against)
STREAM_FRAMES_AVAILABLE = False
try:
    _lib.yeptris_stream_iter_slice.argtypes = [_p, ctypes.POINTER(_sz),
                                               ctypes.POINTER(_sz)]
    _lib.yeptris_stream_iter_slice.restype = ctypes.c_int
    _lib.yeptris_stream_iter_buffer.argtypes = [
        _p, ctypes.POINTER(ctypes.POINTER(ctypes.c_char)),
        ctypes.POINTER(_sz)]
    _lib.yeptris_stream_iter_buffer.restype = ctypes.c_int
    STREAM_FRAMES_AVAILABLE = True
except AttributeError:
    pass


# --- the plan face (plan.h, yeptris#293; C 0.6.77) ----------------------
# YEP_PLAN_* — the compiled plan's column kinds (plan.h).
PLAN_INT = 0
PLAN_FLOAT = 1
PLAN_STR = 2
PLAN_BOOL = 3

# yeptris_plan_str (plan.h): the DOM leg's string cell — a (ptr, len)
# view into the document. POINTER(c_char), NOT c_char_p: a c_char_p
# field hands back an immutable copy scanned to the first NUL, and
# scalar bytes legally carry NULs (the recorder arena's law, above).
class _PlanStr(ctypes.Structure):
    _fields_ = [("p", ctypes.POINTER(ctypes.c_char)),
                ("len", ctypes.c_size_t)]


PLAN_AVAILABLE = False
try:
    _lib.yeptris_plan_compile.argtypes = [ctypes.c_char_p, _sz,
                                          ctypes.POINTER(ctypes.c_int)]
    _lib.yeptris_plan_compile.restype = _p
    _lib.yeptris_plan_free.argtypes = [_p]
    _lib.yeptris_plan_column_count.argtypes = [_p]
    _lib.yeptris_plan_column_count.restype = _sz
    # both walks return a malloc'd result the caller frees FIRST (the
    # result borrows the tape/document it was walked against)
    _lib.yeptris_tape_plan_walk.argtypes = [_p, _p, ctypes.POINTER(ctypes.c_int)]
    _lib.yeptris_tape_plan_walk.restype = _p
    _lib.yeptris_document_plan_walk.argtypes = [_p, _p, ctypes.POINTER(ctypes.c_int)]
    _lib.yeptris_document_plan_walk.restype = _p
    _lib.yeptris_plan_result_free.argtypes = [_p]
    _lib.yeptris_plan_result_rows.argtypes = [_p]
    _lib.yeptris_plan_result_rows.restype = _sz
    _lib.yeptris_plan_result_kind.argtypes = [_p, _sz]
    _lib.yeptris_plan_result_kind.restype = ctypes.c_int
    _lib.yeptris_plan_result_ints.argtypes = [_p, _sz]
    _lib.yeptris_plan_result_ints.restype = ctypes.POINTER(ctypes.c_int64)
    _lib.yeptris_plan_result_floats.argtypes = [_p, _sz]
    _lib.yeptris_plan_result_floats.restype = ctypes.POINTER(ctypes.c_double)
    _lib.yeptris_plan_result_str_offs.argtypes = [_p, _sz]
    _lib.yeptris_plan_result_str_offs.restype = ctypes.POINTER(ctypes.c_uint32)
    _lib.yeptris_plan_result_str_lens.argtypes = [_p, _sz]
    _lib.yeptris_plan_result_str_lens.restype = ctypes.POINTER(ctypes.c_uint32)
    _lib.yeptris_plan_result_strs.argtypes = [_p, _sz]
    _lib.yeptris_plan_result_strs.restype = ctypes.POINTER(_PlanStr)
    _lib.yeptris_plan_result_nulls.argtypes = [_p, _sz]
    _lib.yeptris_plan_result_nulls.restype = ctypes.POINTER(ctypes.c_uint8)
    PLAN_AVAILABLE = True
except AttributeError:
    pass

# --- the compact JSON tape (tape.h, TODO.restructure/85; C 0.6.77) ------
# YEP_T_* — the record kinds column (tape.h).
TAPE_DOC = 0
TAPE_NULL = 1
TAPE_TRUE = 2
TAPE_FALSE = 3
TAPE_INT = 4
TAPE_FLOAT = 5
TAPE_STR = 6
TAPE_SEQ_OPEN = 7
TAPE_MAP_OPEN = 8
TAPE_CLOSE = 9
TAPE_NUM = 10  # the lenient route only: the deferred number contract

# yeptris_json_tape (tape.h): the CALLER allocates the tape struct, so
# the FFI mirrors the header's layout — count/pointers (LP64-sized via
# c_size_t/c_void_p), the two route ints, the convert-discovered
# int_min, the source pointer + length (the spans borrow it), the
# block base and the two long-key verdict bytes. The column pointers
# are TYPED (uint8/uint32): hosts index them after
# yeptris_tape_columns materializes the lenient route's lazy columns.
# Zero-initialized is exactly the C "{0}" the parse functions expect
# (they memset it).
class _JsonTape(ctypes.Structure):
    _fields_ = [("count", _sz),
                ("kinds", ctypes.POINTER(ctypes.c_uint8)),
                ("offs", ctypes.POINTER(ctypes.c_uint32)),
                ("lens", ctypes.POINTER(ctypes.c_uint32)),
                ("recs", _p), ("_cols_ready", ctypes.c_int),
                ("_rec_primary", ctypes.c_int), ("int_min", ctypes.c_int64),
                ("_src", _p), ("_srclen", _sz), ("_block", _p),
                ("_long_key_known", ctypes.c_ubyte),
                ("_long_key", ctypes.c_ubyte)]


assert ctypes.sizeof(_JsonTape) == 88  # LP64: 82 bytes of fields -> 88

TAPE_AVAILABLE = False
try:
    _lib.yeptris_parse_json_tape.argtypes = [ctypes.c_char_p, _sz,
                                             ctypes.POINTER(_JsonTape)]
    _lib.yeptris_parse_json_tape.restype = ctypes.c_int
    _lib.yeptris_parse_json_tape_lenient.argtypes = [ctypes.c_char_p, _sz,
                                                     ctypes.POINTER(_JsonTape)]
    _lib.yeptris_parse_json_tape_lenient.restype = ctypes.c_int
    _lib.yeptris_tape_free.argtypes = [ctypes.POINTER(_JsonTape)]
    _lib.yeptris_tape_columns.argtypes = [ctypes.POINTER(_JsonTape)]
    _lib.yeptris_tape_columns.restype = ctypes.c_int
    _lib.yeptris_tape_convert.argtypes = [ctypes.POINTER(_JsonTape), _sz, _sz,
                                          ctypes.POINTER(ctypes.c_int64),
                                          ctypes.POINTER(ctypes.c_double)]
    _lib.yeptris_tape_convert.restype = _sz
    TAPE_AVAILABLE = True
except AttributeError:
    pass

# --- the pull / iterparse event faces (events.h) ------------------------
# YeptrisEvent (events.h): one event; the string fields point into the
# owning model's storage and are valid UNTIL THE NEXT CALL — the event
# wrappers copy at hand-out so the window never escapes into Python.
class _Event(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("style", ctypes.c_int),
                ("flow", ctypes.c_int), ("explicit_marker", ctypes.c_int),
                ("implicit", ctypes.c_int),
                ("value", ctypes.POINTER(ctypes.c_char)),
                ("value_len", _sz),
                ("anchor", ctypes.POINTER(ctypes.c_char)),
                ("anchor_len", _sz),
                ("tag", ctypes.POINTER(ctypes.c_char)),
                ("tag_len", _sz),
                ("line", ctypes.c_uint32), ("col", ctypes.c_uint32),
                ("end_line", ctypes.c_uint32), ("end_col", ctypes.c_uint32)]


assert ctypes.sizeof(_Event) == 24 + 48 + 16  # LP64: 5 ints (+pad), 6 ptrs, 4 u32

EVENTS_AVAILABLE = False
try:
    _lib.yeptris_pull_new.argtypes = [ctypes.c_char_p, _sz]
    _lib.yeptris_pull_new.restype = _p
    _lib.yeptris_pull_next.argtypes = [_p]
    _lib.yeptris_pull_next.restype = ctypes.POINTER(_Event)
    _lib.yeptris_pull_next_batch.argtypes = [_p, ctypes.POINTER(_Event), _sz]
    _lib.yeptris_pull_next_batch.restype = _sz
    _lib.yeptris_pull_status.argtypes = [_p, ctypes.POINTER(ctypes.c_uint32),
                                         ctypes.POINTER(ctypes.c_uint32)]
    _lib.yeptris_pull_status.restype = ctypes.c_int
    _lib.yeptris_pull_free.argtypes = [_p]
    _lib.yeptris_iterparse_new.argtypes = [ctypes.c_char_p, _sz]
    _lib.yeptris_iterparse_new.restype = _p
    _lib.yeptris_iterparse_next.argtypes = [_p, ctypes.POINTER(_sz)]
    _lib.yeptris_iterparse_next.restype = ctypes.POINTER(_Event)
    _lib.yeptris_iterparse_status.argtypes = [
        _p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    _lib.yeptris_iterparse_status.restype = ctypes.c_int
    _lib.yeptris_iterparse_free.argtypes = [_p]
    EVENTS_AVAILABLE = True
except AttributeError:
    pass

# The DOM leg of the plan walk needs a parsed document handle:
# yeptris_parse (12_CORE) / yeptris_parse_ex (any schema) — present
# since the first public releases, declared flat (no feature-detect).
_lib.yeptris_parse.argtypes = [ctypes.c_char_p, _sz, ctypes.POINTER(ctypes.c_int)]
_lib.yeptris_parse.restype = _p
_lib.yeptris_parse_ex.argtypes = [ctypes.c_char_p, _sz,
                                  ctypes.POINTER(_ParseOptions),
                                  ctypes.POINTER(ctypes.c_int)]
_lib.yeptris_parse_ex.restype = _p


def last_error():
    line = ctypes.c_uint32(0)
    col = ctypes.c_uint32(0)
    msg = _lib.yeptris_last_error(ctypes.byref(line), ctypes.byref(col))
    return (msg.decode("utf-8", "replace") if msg else "parse error",
            line.value, col.value)


def _stage_error(status: int, msg: str, line: int, col: int) -> YeptrisError:
    """Engine status -> the taxonomy stage. The engine fuses scanning
    into its single parse status, so scan- and parse-stage rejections
    both surface as ParserError (ScannerError is reserved — see
    _errors.py); ERROR_ENCODING is the reader stage; anything else
    (memory/arg/internal) stays the legacy ParseError so every load
    failure keeps catching as yeptris.ParseError."""
    if status == ERROR_ENCODING:
        return ReaderError(msg, line, col)
    if status in (ERROR_PARSE, ERROR_DEPTH):
        return ParserError(msg, line, col)
    return ParseError(msg, line, col)


def parse_json_strict(b: bytes) -> None:
    """The RFC 8259 validator gate: raises ParseError on anything
    strict JSON rejects (TODO.restructure/41 — the ctypes JSON
    engine's gate; the native engine validates in its own walk)."""
    st = ctypes.c_int(0)
    doc = _lib.yeptris_parse_json(b, len(b), ctypes.byref(st))
    if st.value != OK or not doc:
        msg, line, col = last_error()
        raise ParseError(msg or "invalid JSON", line, col)
    _lib.yeptris_document_free(doc)


def free_buffer(buf) -> None:
    if buf:
        libc_free(ctypes.cast(buf, ctypes.c_void_p))


def read_owned(ptr, length) -> bytes:
    """The serialize* contract: caller copies then frees."""
    if not ptr:
        return b""
    try:
        return ctypes.string_at(ptr, length)
    finally:
        free_buffer(ptr)


def copy_span(ptr, length):
    """The event-view contract: the string lives in the model's arena
    and is valid UNTIL THE NEXT CALL — the caller copies within the
    window. NULL (length 0 views the engine never materialized, e.g.
    the empty scalar) reads as None, mirroring the C contract."""
    if not ptr:
        return None
    return ctypes.string_at(ptr, length)


class _ValueColumns(ctypes.Structure):
    """YeptrisValueColumns (values.h): one carved block + the arena."""
    _fields_ = [("count", ctypes.c_size_t), ("arena_len", ctypes.c_size_t),
                ("payloads", ctypes.c_void_p), ("offs", ctypes.c_void_p),
                ("lens", ctypes.c_void_p), ("kinds", ctypes.c_void_p),
                ("tags", ctypes.c_void_p), ("is_keys", ctypes.c_void_p),
                ("bools", ctypes.c_void_p), ("arena", ctypes.c_void_p)]


# Columnar value drain (libyeptris > 0.1.1): feature-detected; the
# record drain below is the fallback on older libraries.
COLUMNS = hasattr(_lib, "yeptris_value_drain_columns")
if COLUMNS:
    _lib.yeptris_value_drain_columns.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
        ctypes.POINTER(_ValueColumns)]
    _lib.yeptris_value_drain_columns.restype = ctypes.c_int
    _lib.yeptris_value_free_columns.argtypes = [ctypes.POINTER(_ValueColumns)]
    _lib.yeptris_value_free_columns.restype = None


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
            raise _stage_error(st, msg, line, col)
        n = ctypes.c_size_t(0)
        raw = _lib.yeptris_recorder_records(rec, ctypes.byref(n))
        records = ctypes.string_at(raw, n.value * RECORD_SIZE) if n.value else b""
        alen = ctypes.c_size_t(0)
        arena_p = _lib.yeptris_recorder_arena(rec, ctypes.byref(alen))
        arena = ctypes.string_at(arena_p, alen.value) if alen.value else b""
        return records, arena
    finally:
        _lib.yeptris_recorder_free(rec)


def drain_columns(yaml: bytes, schema: int):
    """The value stream as parallel typed columns, one read each.

    Returns (kinds, tags, is_keys, bools, offs, lens, payloads, arena,
    closer) — bytes for the byte columns, array.array for the wider
    ones, and a callable that frees the C-side block (call it when the
    walk has copied out everything it needs; every Python value is
    arena-slice-copied, so freeing after the walk is always safe).
    """
    if not COLUMNS:
        raise YeptrisError("columnar drain unavailable in this libyeptris")
    c = _ValueColumns()
    st = _lib.yeptris_value_drain_columns(yaml, len(yaml), schema, ctypes.byref(c))
    if st != OK:
        msg, line, col = last_error()
        raise _stage_error(st, msg, line, col)

    def _close():
        _lib.yeptris_value_free_columns(ctypes.byref(c))

    try:
        n = c.count
        if n:
            kinds = ctypes.string_at(c.kinds, n)
            tags = ctypes.string_at(c.tags, n)
            is_keys = ctypes.string_at(c.is_keys, n)
            bools = ctypes.string_at(c.bools, n)
            offs = array.array("I", ctypes.string_at(c.offs, 4 * n))
            lens = array.array("I", ctypes.string_at(c.lens, 4 * n))
            pays = array.array("q", ctypes.string_at(c.payloads, 8 * n))
        else:
            kinds = tags = is_keys = bools = b""
            offs = lens = array.array("I")
            pays = array.array("q")
        arena = ctypes.string_at(c.arena, c.arena_len) if c.arena_len else b""
        cols = (kinds, tags, is_keys, bools, offs, lens, pays, arena, _close)
        _close = None  # ownership passed to the caller
        return cols
    finally:
        if _close is not None:
            _close()
