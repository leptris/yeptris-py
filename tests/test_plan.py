"""The compiled plan face (plan.h, C 0.6.77, yeptris#293): compile
once from a strict-JSON spec, walk a document (DOM leg) or a JSON tape
(tape leg) in one C pass, read typed columns.

Pinned law (plan.h + the rs binding's `plan` face, mirrored here):
- the columns are the spec's children, in spec order; INT columns read
  int64, BOOL columns read 0/1 through the same ints lane, FLOAT
  columns read doubles, STR columns read the parsed content (DOM leg)
  or the RAW source span (tape leg — the tape law).
- explicit null and missing leaves mark the slot null (the per-column
  bitmap; the typed slot is then unspecified).
- a walk against a document that does not carry the rows container is
  a shape disagreement (the engine's PARSE status), not a crash.
"""

import pytest

from yeptris import _ffi as F
from yeptris import plan as plan_mod
from yeptris import tape as tape_mod
from yeptris._errors import ParseError, YeptrisError
from yeptris.plan import Plan
from yeptris.tape import parse_tape

pytestmark = pytest.mark.skipif(
    not F.PLAN_AVAILABLE,
    reason="the loaded libyeptris predates the plan face (C 0.6.77)")

SPEC = ('{"kind": "seq", "path": "items", "children": ['
        '{"name": "id", "kind": "int"},'
        '{"name": "tag", "kind": "str"},'
        '{"name": "ok", "kind": "bool"},'
        '{"name": "v", "kind": "float"}]}')

YAML_DOC = (
    "items:\n"
    "  - {id: 7, tag: x, ok: true, v: 1.5}\n"
    "  - {id: 8, tag: y, ok: false, v: 2.5}\n"
    "  - {tag: z}\n"           # id/ok/v missing -> null slots
)

EXPECTED_DICTS = [
    {"id": 7, "tag": "x", "ok": True, "v": 1.5},
    {"id": 8, "tag": "y", "ok": False, "v": 2.5},
    {"id": None, "tag": "z", "ok": None, "v": None},
]


# --- compile ---------------------------------------------------------------

def test_compile_and_column_count():
    plan = Plan(SPEC)
    try:
        assert plan.column_count() == 4
    finally:
        plan.close()


def test_compile_rejects_malformed_spec_json():
    with pytest.raises(YeptrisError):
        Plan("not json")


def test_compile_rejects_bad_shapes():
    for spec in ('{"kind": "seq"}',                    # no children
                 '{"kind": "seq", "children": []}',    # empty children
                 '{"kind": "bogus", "children": '
                 '[{"name": "a", "kind": "int"}]}'):   # unknown kind
        with pytest.raises(YeptrisError):
            Plan(spec)


def test_input_forms():
    plan = Plan(SPEC.encode())
    plan.close()
    plan = Plan(SPEC)  # str rides utf-8
    plan.close()


# --- the DOM leg (YAML + JSON through one walk) ----------------------------

def test_walk_yaml_seq_of_maps():
    plan = Plan(SPEC)
    try:
        r = plan.walk(YAML_DOC)
        assert r.rows == 3
        assert r.kinds == ["int", "str", "bool", "float"]
        assert list(r.ints(0)) == [7, 8, 0]      # the null slot is unspecified
        assert r.strs(1) == ["x", "y", "z"]
        assert list(r.ints(2)) == [1, 0, 0]      # BOOL reads 0/1
        assert list(r.floats(3)) == [1.5, 2.5, 0.0]
        assert r.nulls(0) == b"\x00\x00\x01"
        assert r.nulls(1) == b"\x00\x00\x00"
        assert r.nulls(2) == b"\x00\x00\x01"
        assert r.nulls(3) == b"\x00\x00\x01"
    finally:
        plan.close()


def test_walk_to_dicts():
    plan = Plan(SPEC)
    try:
        assert plan.walk(YAML_DOC).to_dicts() == EXPECTED_DICTS
    finally:
        plan.close()


def test_column_convenience_forms_typed_nones():
    plan = Plan(SPEC)
    try:
        r = plan.walk(YAML_DOC)
        assert r.column(0) == [7, 8, None]
        assert r.column(1) == ["x", "y", "z"]
        assert r.column(2) == [True, False, None]
        assert r.column(3) == [1.5, 2.5, None]
    finally:
        plan.close()


def test_walk_explicit_null_marks_the_slot():
    plan = Plan(SPEC)
    try:
        r = plan.walk("items:\n  - id: null\n")
        assert r.rows == 1
        assert r.nulls(0) == b"\x01"
        assert r.column(0) == [None]
    finally:
        plan.close()


def test_walk_json_root_seq_with_a_pathless_spec():
    # the DOM leg takes strict JSON too (JSON is YAML flow); a root
    # seq needs the pathless spec — the rows container IS the root
    spec = ('{"kind": "seq", "children": ['
            '{"name": "id", "kind": "int"},'
            '{"name": "tag", "kind": "str"},'
            '{"name": "ok", "kind": "bool"},'
            '{"name": "v", "kind": "float"}]}')
    plan = Plan(spec)
    try:
        r = plan.walk('[{"id": 1, "tag": "a", "ok": true, "v": 0.5}]')
        assert r.to_dicts() == [{"id": 1, "tag": "a", "ok": True, "v": 0.5}]
    finally:
        plan.close()


def test_walk_json_against_a_spec_path_the_doc_lacks():
    plan = Plan(SPEC)
    try:
        with pytest.raises(ParseError):
            plan.walk('[{"id": 1}]')  # root seq, but the spec paths "items"
    finally:
        plan.close()


def test_walk_map_of_rows():
    plan = Plan('{"kind": "map", "path": "rows", "children": '
                '[{"name": "n", "kind": "int"}]}')
    try:
        r = plan.walk("rows:\n  a: {n: 1}\n  b: {n: 2}\n")
        assert r.to_dicts() == [{"n": 1}, {"n": 2}]
    finally:
        plan.close()


def test_walk_segmented_path():
    plan = Plan('{"kind": "seq", "path": ["data", "items"], "children": '
                '[{"name": "n", "kind": "int"}]}')
    try:
        r = plan.walk("data:\n  items:\n    - n: 5\n")
        assert r.to_dicts() == [{"n": 5}]
    finally:
        plan.close()


def test_walk_empty_rows():
    plan = Plan(SPEC)
    try:
        r = plan.walk("items: []\n")
        assert r.rows == 0
        assert len(r) == 0
        assert list(r.ints(0)) == []
        assert r.strs(1) == []
        assert r.nulls(3) == b""
    finally:
        plan.close()


def test_walk_schema_kwarg_rides_parse_time_typing():
    plan = Plan('{"kind": "seq", "path": "items", "children": '
                '[{"name": "ok", "kind": "bool"}]}')
    try:
        # the typed extraction rides the parse-time tag ids: under the
        # 1.1-compat resolver `y` IS a boolean
        r = plan.walk("items:\n  - ok: y\n", schema=F.SCHEMA_11_COMPAT)
        assert r.to_dicts() == [{"ok": True}]
    finally:
        plan.close()


def test_shape_disagreement_is_a_parse_stage_error():
    plan = Plan(SPEC)
    try:
        with pytest.raises(ParseError) as ei:
            plan.walk("other: []\n")
        assert "path 'items'" in str(ei.value)
    finally:
        plan.close()


def test_walk_rejects_unparseable_documents():
    plan = Plan(SPEC)
    try:
        with pytest.raises(YeptrisError):
            plan.walk("items: [1,\n")
    finally:
        plan.close()


# --- the tape leg ----------------------------------------------------------

def test_walk_tape_and_cross_leg_equivalence():
    plan = Plan(SPEC)
    try:
        t = parse_tape('{"items": [{"id": 7, "tag": "x", "ok": true, '
                       '"v": 1.5}, {"id": 8}]}')
        try:
            r = plan.walk_tape(t)
            assert r.rows == 2
            assert r.to_dicts() == [{"id": 7, "tag": "x", "ok": True,
                                     "v": 1.5},
                                    {"id": 8, "tag": None, "ok": None,
                                     "v": None}]
            # both rows carry an id; row 1 lacks tag/ok/v
            assert r.nulls(0) == b"\x00\x00"
            assert r.nulls(1) == b"\x00\x01"
            assert r.nulls(3) == b"\x00\x01"
            # the DOM leg over the same data answers the same rows
            r2 = plan.walk('items:\n  - {id: 7, tag: x, ok: true, v: 1.5}\n'
                           "  - {id: 8}\n")
            assert r2.to_dicts() == r.to_dicts()
        finally:
            t.close()
    finally:
        plan.close()


def test_tape_leg_str_columns_carry_raw_spans():
    plan = Plan('{"kind": "seq", "children": [{"name": "s", "kind": "str"}]}')
    try:
        t = parse_tape('[{"s": "a\\nb"}]')
        try:
            r = plan.walk_tape(t)
            # the tape law: the span is the raw source bytes, escapes
            # untouched — the column shows them as the source spelled them
            assert r.strs(0) == ["a\\nb"]
        finally:
            t.close()
    finally:
        plan.close()


# --- the accessors ---------------------------------------------------------

def test_accessor_kind_guards():
    plan = Plan(SPEC)
    try:
        r = plan.walk(YAML_DOC)
        with pytest.raises(TypeError):
            r.ints(1)   # str column
        with pytest.raises(TypeError):
            r.floats(0)  # int column
        with pytest.raises(TypeError):
            r.strs(3)   # float column
        with pytest.raises(IndexError):
            r.nulls(9)
    finally:
        plan.close()


def test_result_kinds_property_is_a_copy():
    plan = Plan(SPEC)
    try:
        r = plan.walk(YAML_DOC)
        kinds = r.kinds
        kinds.append("bogus")
        assert r.kinds == ["int", "str", "bool", "float"]
    finally:
        plan.close()


# --- the lifetime law ------------------------------------------------------

def test_close_is_idempotent_and_use_after_close_raises():
    plan = Plan(SPEC)
    plan.close()
    plan.close()
    with pytest.raises(YeptrisError):
        plan.walk("items: []")
    with pytest.raises(YeptrisError):
        plan.column_count()


def test_context_manager_closes():
    with Plan(SPEC) as plan:
        assert plan.column_count() == 4
    with pytest.raises(YeptrisError):
        plan.column_count()


def test_garbage_collected_plans_close_the_engine_handle():
    import gc
    import weakref
    plan = Plan(SPEC)
    ref = weakref.ref(plan)
    del plan  # no explicit close: the __del__ finalizer owns the handle
    gc.collect()
    assert ref() is None


def test_walk_tape_requires_an_open_tape():
    plan = Plan(SPEC)
    try:
        t = parse_tape("[]")
        t.close()
        with pytest.raises(YeptrisError):
            plan.walk_tape(t)
    finally:
        plan.close()


def test_capability_gate_names_the_floor():
    assert plan_mod.available() == bool(F.PLAN_AVAILABLE)
    assert tape_mod.available() == bool(F.TAPE_AVAILABLE)
