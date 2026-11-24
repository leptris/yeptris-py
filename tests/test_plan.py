"""The compiled-plan columnar walk (yeptris/plan.h, engine #293): a
plan compiled once from a strict-JSON spec, applied to a parsed
document (the YAML/JSON front door) or to a JSON tape, in ONE C pass
producing typed COLUMNAR results — no per-node host dispatch.

Pinned law (the header's contract, yeptris-rs plan:: is the mirror):
- leaf kinds are int / float / str / bool; kind is "seq" or "map"
  (the ROWS container's shape at the root).
- path selects the rows container: a string, or an ARRAY of strings
  for a segmented (nested) path. Empty/absent: the seq root itself.
- explicit null AND missing leaves mark the slot null.
- the tape leg exposes string columns as (off,len) spans into the
  source; the document leg as (ptr,len) views into the document.
- a malformed spec rejects at COMPILE (ARG/PARSE); a document whose
  shape disagrees with the plan rejects at WALK (PARSE).
"""

import pytest

from yeptris import ParseError, YeptrisError
from yeptris import _ffi as F
from yeptris.plan import compile_plan
from yeptris.tape import parse_tape

pytestmark = pytest.mark.skipif(
    not F.PLAN_AVAILABLE,
    reason="the loaded libyeptris predates the plan face (plan.h)")


SEQ_SPEC = ('{"kind":"seq","path":"items","children":['
            '{"name":"id","kind":"int"},'
            '{"name":"name","kind":"str"},'
            '{"name":"ratio","kind":"float"},'
            '{"name":"ok","kind":"bool"}]}')

SEQ_YAML = (
    "items:\n"
    "  - id: 7\n"
    "    name: alpha\n"
    "    ratio: 0.5\n"
    "    ok: true\n"
    "  - id: 8\n"
    "    name: beta\n"
    "    ratio: 1.25\n"
    "    ok: false\n"
)


def test_compile_and_column_count():
    plan = compile_plan(SEQ_SPEC)
    try:
        assert plan.column_count == 4
    finally:
        plan.close()


def test_context_manager_closes():
    with compile_plan(SEQ_SPEC) as plan:
        assert plan.column_count == 4


def test_document_walk_typed_columns():
    with compile_plan(SEQ_SPEC) as plan:
        result = plan.walk(SEQ_YAML)
        try:
            assert result.rows == 2
            assert result.kinds == ("int", "str", "float", "bool")
            assert result.column(0) == [7, 8]
            assert result.column(1) == ["alpha", "beta"]
            assert result.column(2) == [0.5, 1.25]
            assert result.column(3) == [True, False]
            assert result.columns() == [
                [7, 8], ["alpha", "beta"], [0.5, 1.25], [True, False]]
        finally:
            result.close()


def test_document_walk_json_input():
    # the walk door is the YAML front door — JSON rides it (JSON is a
    # YAML subset under the core schema)
    data = ('{"items": [{"id": 1, "name": "a", "ratio": 2.0, "ok": true},'
            ' {"id": 2, "name": "b", "ratio": 0.0, "ok": false}]}')
    with compile_plan(SEQ_SPEC) as plan:
        result = plan.walk(data)
        try:
            assert result.rows == 2
            assert result.column(0) == [1, 2]
            assert result.column(1) == ["a", "b"]
        finally:
            result.close()


def test_nulls_missing_and_explicit():
    yaml = ("items:\n"
            "  - id: 1\n"
            "  - id: 2\n"
            "    name: ~\n")
    with compile_plan(SEQ_SPEC) as plan:
        result = plan.walk(yaml)
        try:
            assert result.rows == 2
            assert result.column(0) == [1, 2]
            assert result.column(1) == [None, None]
            assert result.column(2) == [None, None]
            assert result.column(3) == [None, None]
            assert result.nulls(0) == [False, False]
            assert result.nulls(1) == [True, True]
        finally:
            result.close()


def test_map_kind_root():
    spec = ('{"kind":"map","path":"rows","children":['
            '{"name":"id","kind":"int"},{"name":"v","kind":"str"}]}')
    data = "rows:\n  a:\n    id: 1\n    v: x\n  b:\n    id: 2\n    v: y\n"
    with compile_plan(spec) as plan:
        result = plan.walk(data)
        try:
            assert result.rows == 2
            assert result.column(0) == [1, 2]
            assert result.column(1) == ["x", "y"]
        finally:
            result.close()


def test_segmented_path():
    spec = ('{"kind":"seq","path":["outer","inner"],'
            '"children":[{"name":"n","kind":"int"}]}')
    data = "outer:\n  inner:\n    - n: 5\n    - n: 6\n"
    with compile_plan(spec) as plan:
        result = plan.walk(data)
        try:
            assert result.rows == 2
            assert result.column(0) == [5, 6]
        finally:
            result.close()


def test_to_rows_named_convenience():
    with compile_plan(SEQ_SPEC) as plan:
        result = plan.walk(SEQ_YAML)
        try:
            assert result.to_rows() == [
                {"id": 7, "name": "alpha", "ratio": 0.5, "ok": True},
                {"id": 8, "name": "beta", "ratio": 1.25, "ok": False},
            ]
        finally:
            result.close()


def test_unicode_str_column():
    data = "items:\n  - name: é中文\n"
    spec = ('{"kind":"seq","path":"items",'
            '"children":[{"name":"name","kind":"str"}]}')
    with compile_plan(spec) as plan:
        result = plan.walk(data)
        try:
            assert result.column(0) == ["é中文"]
        finally:
            result.close()


def test_empty_collection_zero_rows():
    with compile_plan(SEQ_SPEC) as plan:
        result = plan.walk("items: []\n")
        try:
            assert result.rows == 0
            assert result.column(0) == []
            assert result.columns() == [[], [], [], []]
        finally:
            result.close()


def test_tape_leg():
    json = b'{"items": [{"id": 7, "ok": true}, {"id": 8, "ok": false}]}'
    spec = ('{"kind":"seq","path":"items","children":['
            '{"name":"id","kind":"int"},{"name":"ok","kind":"bool"}]}')
    with parse_tape(json) as tape, compile_plan(spec) as plan:
        result = plan.walk_tape(tape)
        try:
            assert result.rows == 2
            assert result.column(0) == [7, 8]
            assert result.column(1) == [True, False]
        finally:
            result.close()


def test_tape_leg_str_column_reads_spans():
    json = b'{"items": [{"s": "aa"}, {"s": "bbb"}]}'
    spec = ('{"kind":"seq","path":"items",'
            '"children":[{"name":"s","kind":"str"}]}')
    with parse_tape(json) as tape, compile_plan(spec) as plan:
        result = plan.walk_tape(tape)
        try:
            assert result.column(0) == ["aa", "bbb"]
        finally:
            result.close()


def test_malformed_spec_rejects_at_compile():
    with pytest.raises((ParseError, YeptrisError)):
        compile_plan('{"kind":"seq","children":[{"name":"x","kind":"nope"}]}')
    with pytest.raises((ParseError, YeptrisError)):
        compile_plan("not json at all")


def test_shape_disagreement_rejects_at_walk():
    spec = ('{"kind":"seq","path":"items",'
            '"children":[{"name":"id","kind":"int"}]}')
    with compile_plan(spec) as plan:
        with pytest.raises(ParseError):
            plan.walk("other:\n  - id: 1\n")


def test_container_at_planned_leaf_stays_null():
    # the engine's tolerant leaf law: a container (or a mismatched
    # scalar) at a planned leaf marks the slot null and the walk goes
    # on — only a ROOT-shape disagreement rejects
    spec = ('{"kind":"seq","path":"items",'
            '"children":[{"name":"id","kind":"int"}]}')
    with compile_plan(spec) as plan:
        result = plan.walk("items:\n  - id: [1]\n  - id: 2\n")
        try:
            assert result.rows == 2
            assert result.column(0) == [None, 2]
            assert result.nulls(0) == [True, False]
        finally:
            result.close()
