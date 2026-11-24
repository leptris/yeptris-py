"""The compiled-plan columnar walk (plan.h, engine #293): a plan
compiled ONCE from a strict-JSON spec, applied to a parsed document
or to a JSON tape in ONE C pass producing typed COLUMNAR results —
no Python tree, no per-node host dispatch (yeptris-rs ``plan::`` is
the sibling shape).

The spec (strict JSON):

    {"kind":"seq","path":"items","children":[
        {"name":"id","kind":"int"}, {"name":"ok","kind":"bool"}, ...]}

- ``kind`` is "seq" or "map" — the ROWS container's shape at the root.
- ``path`` selects the rows container: a string, or an ARRAY of
  strings for a segmented (nested) path. Empty/absent: the seq root.
- leaf kinds: int, float, str, bool. Explicit null and missing leaves
  mark the slot null.

Two walk doors: ``Plan.walk`` (the yeptris_parse front door — YAML or
JSON) and ``Plan.walk_tape`` (a ``yeptris.tape`` tape). The C result
is a hand-off format whose string spans borrow the walked document —
this binding drains the whole result into Python values at walk time
and frees the C side immediately, so no Python object holds a borrow
(the free-before-source ordering law never becomes the caller's
problem).
"""

from __future__ import annotations

import ctypes
import json as _stdlib_json

from . import _ffi as F

__all__ = ["Plan", "PlanResult", "available", "compile_plan",
           "PLAN_INT", "PLAN_FLOAT", "PLAN_STR", "PLAN_BOOL"]

# column kinds (plan.h)
PLAN_INT = 0
PLAN_FLOAT = 1
PLAN_STR = 2
PLAN_BOOL = 3

_KIND_NAMES = {PLAN_INT: "int", PLAN_FLOAT: "float", PLAN_STR: "str",
               PLAN_BOOL: "bool"}


def available() -> bool:
    """Whether the loaded libyeptris carries the plan face."""
    return bool(F.PLAN_AVAILABLE)


def _require() -> None:
    if not F.PLAN_AVAILABLE:
        raise F.YeptrisError(
            "the loaded libyeptris has no plan face; this surface "
            "requires a newer engine (set YEPTRIS_LIB_PATH to a newer "
            "build)")


class PlanResult:
    """One walk's typed columns, drained into Python values.

    ``rows`` is the row count; ``kinds`` the per-column leaf kinds
    ("int"/"float"/"str"/"bool"); ``column(i)`` the copied values with
    ``None`` at null/missing slots; ``nulls(i)`` the per-slot markers
    (True = null/missing); ``to_rows()`` zips the spec's names with
    the columns into one dict per row. ``close()`` is accepted for
    symmetry with the other faces and frees nothing — every byte the
    engine produced was copied at walk time.
    """

    def __init__(self, rows, kinds, columns, nulls, names):
        self._rows = rows
        self._kinds = kinds
        self._columns = columns
        self._nulls = nulls
        self._names = names

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def kinds(self) -> tuple:
        return self._kinds

    def column(self, i: int) -> list:
        return self._columns[i]

    def columns(self) -> list:
        return [list(col) for col in self._columns]

    def nulls(self, i: int) -> list:
        return list(self._nulls[i])

    def to_rows(self) -> list:
        return [
            {name: col[row] for name, col in zip(self._names, self._columns)}
            for row in range(self._rows)]

    def close(self) -> None:
        pass

    def __enter__(self) -> "PlanResult":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Plan:
    """A compiled plan. Owns the engine's plan handle (``close()``
    frees it; the context manager form is the idiomatic door)."""

    def __init__(self, spec):
        raise TypeError("compile with compile_plan(spec)")

    @classmethod
    def _compile(cls, spec):
        _require()
        if isinstance(spec, str):
            spec = spec.encode("utf-8")
        st = ctypes.c_int(0)
        raw = F._lib.yeptris_plan_compile(spec, len(spec), ctypes.byref(st))
        if not raw:
            msg, line, col = F.last_error()
            raise F._stage_error(st.value, msg or "invalid plan spec",
                                 line, col)
        plan = cls.__new__(cls)
        plan._raw = raw
        try:
            doc = _stdlib_json.loads(spec.decode("utf-8"))
            plan._names = tuple(str(c["name"]) for c in doc["children"])
        except (ValueError, KeyError, TypeError):
            plan._names = ()
        plan._closed = False
        return plan

    @property
    def column_count(self) -> int:
        self._check_open()
        return int(F._lib.yeptris_plan_column_count(self._raw))

    @property
    def names(self) -> tuple:
        """The spec's leaf names (``to_rows``' key order)."""
        return self._names

    def _check_open(self) -> None:
        if self._closed:
            raise F.YeptrisError("the plan is closed")

    def _drain(self, raw, tape) -> PlanResult:
        """Copy every column into Python values, then free the C
        result AT ONCE (it borrows the walked document / tape source —
        the free-before-source ordering never crosses this function's
        return). The tape leg's string columns read (off,len) spans
        through ``tape``; the document leg's read (ptr,len) cells."""
        rows = int(F._lib.yeptris_plan_result_rows(raw))
        kinds = []
        columns = []
        nulls = []
        try:
            for col in range(self.column_count):
                kind = int(F._lib.yeptris_plan_result_kind(raw, col))
                kinds.append(_KIND_NAMES.get(kind, "int"))
                marks_p = F._lib.yeptris_plan_result_nulls(raw, col)
                marks = ([bool(marks_p[i]) for i in range(rows)]
                         if marks_p else [False] * rows)
                nulls.append(marks)
                if kind in (PLAN_INT, PLAN_BOOL):
                    p = F._lib.yeptris_plan_result_ints(raw, col)
                    if not p:
                        vals = [None] * rows
                    else:
                        vals = [None if marks[i]
                                else (bool(p[i]) if kind == PLAN_BOOL
                                      else int(p[i]))
                                for i in range(rows)]
                elif kind == PLAN_FLOAT:
                    p = F._lib.yeptris_plan_result_floats(raw, col)
                    vals = ([None if marks[i] else p[i]
                             for i in range(rows)]
                            if p else [None] * rows)
                else:  # STR
                    vals = self._drain_strs(raw, tape, rows, col, marks)
                columns.append(vals)
            return PlanResult(rows, tuple(kinds), columns, nulls,
                              self._names)
        finally:
            F._lib.yeptris_plan_result_free(raw)

    @staticmethod
    def _drain_strs(raw, tape, rows, col, marks) -> list:
        if tape is not None:
            # the tape leg: (off,len) spans into the tape's source
            # (the strs accessor is the document leg's only)
            offs = F._lib.yeptris_plan_result_str_offs(raw, col)
            lens = F._lib.yeptris_plan_result_str_lens(raw, col)
            if not offs or not lens:
                return [None] * rows
            return [None if marks[i]
                    else tape._span_bytes(offs[i], lens[i]).decode("utf-8")
                    for i in range(rows)]
        cells = F._lib.yeptris_plan_result_strs(raw, col)
        if not cells:
            return [None] * rows
        out = []
        for i in range(rows):
            if marks[i] or not cells[i].p:
                out.append(None)
            else:
                out.append(ctypes.string_at(cells[i].p,
                                            cells[i].len).decode("utf-8"))
        return out

    def walk(self, data) -> PlanResult:
        """Apply the plan to a YAML or JSON document (the parse front
        door). Raises ParseError when the document's shape disagrees
        with the plan."""
        self._check_open()
        if isinstance(data, str):
            data = data.encode("utf-8")
        st = ctypes.c_int(0)
        doc = F._lib.yeptris_parse(data, len(data), ctypes.byref(st))
        if not doc:
            msg, line, col = F.last_error()
            raise F._stage_error(st.value, msg or "parse failed", line, col)
        st2 = ctypes.c_int(0)
        try:
            raw = F._lib.yeptris_document_plan_walk(doc, self._raw,
                                                    ctypes.byref(st2))
            if not raw:
                msg, line, col = F.last_error()
                raise F._stage_error(st2.value, msg or "plan walk failed",
                                     line, col)
            return self._drain(raw, None)
        finally:
            F._lib.yeptris_document_free(doc)

    def walk_tape(self, tape) -> PlanResult:
        """Apply the plan to a ``yeptris.tape`` tape (the JSON
        columnar route; the lenient tape works — NUM records convert
        at extraction)."""
        self._check_open()
        tape._check_open()
        st = ctypes.c_int(0)
        raw = F._lib.yeptris_tape_plan_walk(ctypes.byref(tape._t), self._raw,
                                            ctypes.byref(st))
        if not raw:
            msg, line, col = F.last_error()
            raise F._stage_error(st.value, msg or "plan walk failed",
                                 line, col)
        return self._drain(raw, tape)

    def close(self) -> None:
        if not self._closed:
            F._lib.yeptris_plan_free(self._raw)
            self._closed = True

    def __enter__(self) -> "Plan":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def compile_plan(spec) -> Plan:
    """Compile a plan from a strict-JSON spec document (see the module
    docstring for the shape). Raises ParseError on a malformed spec."""
    return Plan._compile(spec)
