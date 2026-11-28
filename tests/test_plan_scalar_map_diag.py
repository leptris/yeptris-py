"""The leptris/yeptris-ruby#430 twin, py face: a map-kind plan whose
path leaf resolves to a SCALAR MAP (one record) walks OK but drains
rows=0 — the single record's values are unreachable through the
result API.

Layer diagnosis (2026-11-28; local engine build 0.6.59; the ruby twin
reproduces on 0.6.79, and the mechanism below is structural in the
current engine source):

1. The py wrapper is INNOCENT. Driving the raw C ABI through ctypes —
   every yeptris.plan function bypassed — reproduces it exactly:
   yeptris_plan_compile -> OK, yeptris_parse -> OK,
   yeptris_document_plan_walk -> status OK, yeptris_plan_result_rows
   == 0. The wrapper's `_drain` reads `rows` straight from
   `yeptris_plan_result_rows` and copies whatever the engine produced;
   it invents neither rows nor filters.
2. Both engine doors agree: the document leg AND the tape leg
   (yeptris_tape_plan_walk) answer rows=0 for the same spec, and the
   ruby binding (separate wrapper code, same engine) shows the
   identical result — one engine, three bindings, one answer.
3. The engine is the culprit. DOM leg: `dom_rows_next` (the C repo's
   src/yeptris/plan.c) treats the resolved rows container as a
   CONTAINER OF ROWS — a MAPPING container yields its pair VALUES as
   candidate rows and counts a candidate only when the value is itself
   a MAPPING. A scalar-map leaf therefore resolves as a container
   whose values are scalars; every candidate is skipped and rows
   stays 0. Status stays OK — the silent-zero trap the issue names.
   The tape leg counts repeated children the same way (its
   `find_rows_open` + per-record row count).

The fix belongs in the ENGINE (yeptris plan.c — emit one row for a
scalar-map leaf, or reject/warn when a plan's leaf resolves to a
non-array map): the engine agent owns it, so these pins xfail until
that lands. The pins assert the issue's preferred contract (rows=1,
values populated); if the engine ships the reject/warn contract
instead, flip these to the raised error and move the positive pins
into tests/test_plan.py.
"""

import pytest

from yeptris import _ffi as F
from yeptris.plan import compile_plan
from yeptris.tape import parse_tape

pytestmark = pytest.mark.skipif(
    not F.PLAN_AVAILABLE,
    reason="the loaded libyeptris predates the plan face (plan.h)")

SCALAR_MAP_SPEC = ('{"kind":"map","path":["config","database"],"children":['
                   '{"name":"host","kind":"str"},'
                   '{"name":"port","kind":"int"},'
                   '{"name":"name","kind":"str"},'
                   '{"name":"user","kind":"str"},'
                   '{"name":"password","kind":"str"}]}')
SCALAR_MAP_DOC = ('{"config":{"database":{"host":"localhost","port":5432,'
                  '"name":"myapp","user":"admin","password":"secret"}}}')


@pytest.mark.xfail(
    reason="engine #430-twin: a scalar-map leaf emits rows=0 (plan.c "
           "counts only MAPPING-valued children as rows); the fix is "
           "engine-side, not a py-wrapper change",
    id="scalar-map-leaf-document-leg")
def test_scalar_map_leaf_walks_one_row():
    with compile_plan(SCALAR_MAP_SPEC) as plan:
        result = plan.walk(SCALAR_MAP_DOC)
        try:
            assert result.rows == 1
            assert result.to_rows() == [{
                "host": "localhost", "port": 5432, "name": "myapp",
                "user": "admin", "password": "secret"}]
        finally:
            result.close()


@pytest.mark.xfail(
    reason="engine #430-twin: the tape leg answers rows=0 for a "
           "scalar-map leaf exactly like the document leg",
    id="scalar-map-leaf-tape-leg")
def test_scalar_map_leaf_walks_one_row_on_the_tape_leg():
    with parse_tape(SCALAR_MAP_DOC.encode()) as tape, \
            compile_plan(SCALAR_MAP_SPEC) as plan:
        result = plan.walk_tape(tape)
        try:
            assert result.rows == 1
            assert result.to_rows() == [{
                "host": "localhost", "port": 5432, "name": "myapp",
                "user": "admin", "password": "secret"}]
        finally:
            result.close()


def test_contrast_seq_leaf_under_a_map_kind_path_walks():
    # the shape the engine already answers correctly: the path leaf is
    # an ARRAY of records — each element becomes a row (the issue's
    # working contrast case)
    spec = ('{"kind":"map","path":["dataset","records"],"children":['
            '{"name":"id","kind":"int"}]}')
    with compile_plan(spec) as plan:
        result = plan.walk('{"dataset":{"records":[{"id":1},{"id":2}]}}')
        try:
            assert result.rows == 2
            assert result.column(0) == [1, 2]
        finally:
            result.close()
