"""The compiled plan face (plan.h, yeptris#293): a Descriptor-shaped
plan compiled once from a strict-JSON spec, applied to a document (the
DOM leg — any yeptris-parseable input, YAML or JSON) or to a JSON tape
(:mod:`yeptris.tape`) in ONE C pass, producing typed COLUMNAR output —
no Python tree, no per-node host dispatch.

The spec's shape::

    {"kind": "seq", "path": "items", "children": [
        {"name": "id", "kind": "int"},
        {"name": "tag", "kind": "str"},
        {"name": "ok", "kind": "bool"},
        {"name": "v", "kind": "float"}]}

``kind`` is the ROWS container's shape at the root (``"seq"`` or
``"map"``); ``path`` selects it (a string, or an array of strings for a
segmented path walked mapping by mapping; empty/absent: the root
itself). Explicit null and missing leaves mark the slot null (the
per-column nulls bitmap).

The walk's result is copied into a pure-Python :class:`PlanResult` —
the C result (which borrows the tape or document it was walked
against) is freed before the answer is handed out, so the borrow can
never dangle. BOOL columns read through :meth:`PlanResult.ints` as
0/1, matching the engine's columnar contract.
"""

from __future__ import annotations

import array
import ctypes
import json as _stdlib_json
from typing import Union

from . import _ffi as F
from ._errors import YeptrisError
from ._loader import _as_bytes

__all__ = ["Plan", "PlanResult", "available"]

_Stream = Union[str, bytes]

_KIND_NAMES = {F.PLAN_INT: "int", F.PLAN_FLOAT: "float",
               F.PLAN_STR: "str", F.PLAN_BOOL: "bool"}


def available() -> bool:
    """Whether the loaded libyeptris carries the plan face
    (libyeptris >= 0.6.77)."""
    return bool(F.PLAN_AVAILABLE)


def _require() -> None:
    if not F.PLAN_AVAILABLE:
        raise YeptrisError(
            "the loaded libyeptris has no plan face; this surface "
            "requires libyeptris >= 0.6.77 (set YEPTRIS_LIB_PATH to a "
            "newer build)")


def _spec_shape(spec: bytes):
    """The spec's names + path label, read once with stdlib json —
    pure Python, feeding :meth:`PlanResult.to_dicts` and the walk
    errors' path label. The C compile already validated the spec; this
    read is best-effort and degrades to (None, 'the plan path')."""
    try:
        doc = _stdlib_json.loads(spec)
        if not isinstance(doc, dict):
            return None, "the plan path"
        label = "the plan path"
        path = doc.get("path")
        if isinstance(path, str) and path:
            label = "path %r" % path
        elif isinstance(path, list) and path:
            label = "path %r" % path
        children = doc.get("children")
        names = None
        if isinstance(children, list):
            cand = [c.get("name") for c in children if isinstance(c, dict)]
            if cand and all(isinstance(n, str) for n in cand):
                names = cand
        return names, label
    except (ValueError, AttributeError):
        return None, "the plan path"


_STATUS_NAMES = {
    F.OK: "ok", F.ERROR_PARSE: "parse", F.ERROR_MEMORY: "memory",
    F.ERROR_DEPTH: "depth", F.ERROR_ENCODING: "encoding",
    F.ERROR_IO: "io", F.ERROR_ARG: "argument",
    F.ERROR_UNSUPPORTED: "unsupported", F.ERROR_INTERNAL: "internal",
}


def _walk_error(status: int, path_label: str) -> YeptrisError:
    """The walk's failure contract (plan.h): NULL with *st = PARSE
    (the document shape disagrees — no rows container at the path) or
    MEMORY. The engine does NOT set the error channel here (the channel
    would carry a stale message from an earlier call), so the message
    is composed from the status; the status still raises the taxonomy
    stage (parse-shape disagreement is a ParserError)."""
    return F._stage_error(
        status,
        "plan walk failed (%s): the document does not carry the rows "
        "container at %s" % (_STATUS_NAMES.get(status, status), path_label),
        0, 0)


class Plan:
    """A compiled plan. Owns the engine's plan handle; ``close()``
    frees it (one handle per thread, the document law; immutable after
    compile, so read-only sharing is safe)."""

    def __init__(self, spec: _Stream):
        _require()
        b = _as_bytes(spec)
        st = ctypes.c_int(F.OK)
        handle = F._lib.yeptris_plan_compile(b, len(b), ctypes.byref(st))
        if not handle:
            msg, line, col = F.last_error()
            if st.value == F.ERROR_ARG:
                # the spec's shape is wrong (kind/path/children): the
                # engine answers ARG without a channel message
                raise YeptrisError("invalid plan spec: %s" % (msg or "malformed"))
            raise F._stage_error(st.value, msg, line, col)
        self._plan = handle
        self._names, self._path_label_value = _spec_shape(b)

    def _require_open(self) -> None:
        if not self._plan:
            raise YeptrisError("the plan is closed")

    def close(self) -> None:
        """Free the compiled plan (idempotent)."""
        plan, self._plan = self._plan, None
        if plan:
            F._lib.yeptris_plan_free(plan)

    def __del__(self):  # the GC finalizer over the same liveness flag
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "Plan":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def column_count(self) -> int:
        self._require_open()
        return F._lib.yeptris_plan_column_count(self._plan)

    def walk(self, data: _Stream, *, schema: int = F.SCHEMA_12_CORE) -> "PlanResult":
        """The DOM leg: parse ``data`` (YAML or strict JSON — the
        engine's front door) and apply the plan in one C pass. The
        ``schema`` selects the implicit-typing resolver the typed
        extraction rides (parse-time tag ids — the typing SSOT)."""
        self._require_open()
        b = _as_bytes(data)
        st = ctypes.c_int(F.OK)
        if schema == F.SCHEMA_12_CORE:
            doc = F._lib.yeptris_parse(b, len(b), ctypes.byref(st))
        else:
            opts = F._ParseOptions(schema, 0, 0, 0, 0)
            doc = F._lib.yeptris_parse_ex(b, len(b), ctypes.byref(opts),
                                          ctypes.byref(st))
        if not doc:
            msg, line, col = F.last_error()
            raise F._stage_error(st.value, msg, line, col)
        try:
            walk_st = ctypes.c_int(F.OK)
            r = F._lib.yeptris_document_plan_walk(doc, self._plan,
                                                  ctypes.byref(walk_st))
            if not r:
                raise _walk_error(walk_st.value, self._path_label())
            return _collect(r, self.column_count(), source=None,
                            names=self._names)
        finally:
            F._lib.yeptris_document_free(doc)

    def walk_tape(self, tape) -> "PlanResult":
        """The tape leg: apply the plan to a parsed
        :class:`yeptris.tape.Tape`. String columns expose RAW spans
        into the tape's source (escapes untouched — the tape law)."""
        self._require_open()
        tape._require_open()
        walk_st = ctypes.c_int(F.OK)
        r = F._lib.yeptris_tape_plan_walk(ctypes.byref(tape._raw),
                                          self._plan, ctypes.byref(walk_st))
        if not r:
            raise _walk_error(walk_st.value, self._path_label())
        return _collect(r, self.column_count(), source=tape.source,
                        names=self._names)

    def _path_label(self) -> str:
        return self._path_label_value


class PlanResult:
    """The typed columnar result of one walk — pure Python copies (the
    C result was freed before this object exists; the borrow can never
    dangle). ``rows`` rows, one column per planned leaf, in spec order;
    ``kinds`` spells each column's leaf kind."""

    def __init__(self, rows, kinds, columns, names):
        self._rows = rows
        self._kinds = kinds
        self._columns = columns  # per column: ints/floats/strs/nulls
        self._names = names

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def kinds(self) -> list:
        """Each column's leaf kind: 'int' | 'float' | 'str' | 'bool'."""
        return list(self._kinds)

    def __len__(self) -> int:
        return self._rows

    def _column(self, col: int) -> dict:
        if not 0 <= col < len(self._columns):
            raise IndexError(col)
        return self._columns[col]

    def ints(self, col: int) -> array.array:
        """The INT column (array('q')). BOOL columns read through here
        as 0/1 — the engine's columnar contract."""
        c = self._column(col)
        if self._kinds[col] not in ("int", "bool"):
            raise TypeError("column %d is a %s column, not int/bool"
                            % (col, self._kinds[col]))
        return c["ints"]

    def floats(self, col: int) -> array.array:
        """The FLOAT column (array('d'))."""
        c = self._column(col)
        if self._kinds[col] != "float":
            raise TypeError("column %d is a %s column, not float"
                            % (col, self._kinds[col]))
        return c["floats"]

    def strs(self, col: int) -> list:
        """The STR column (list of str). The tape leg's entries carry
        the RAW source spans (escapes untouched); the DOM leg's carry
        the parsed scalar content."""
        c = self._column(col)
        if self._kinds[col] != "str":
            raise TypeError("column %d is a %s column, not str"
                            % (col, self._kinds[col]))
        return c["strs"]

    def nulls(self, col: int) -> bytes:
        """The null markers (bytes, one per row): 1 = the row's leaf
        was null or missing."""
        return self._column(col)["nulls"]

    def column(self, col: int) -> list:
        """The friendly form: typed values with None at null slots
        (ints as int, bools as bool, floats as float, strs as str)."""
        kind = self._kinds[col]
        c = self._column(col)
        nulls = c["nulls"]
        if kind in ("int", "bool"):
            raw = c["ints"]
            out = []
            for i in range(self._rows):
                if nulls[i]:
                    out.append(None)
                elif kind == "bool":
                    out.append(bool(raw[i]))
                else:
                    out.append(raw[i])
            return out
        if kind == "float":
            raw = c["floats"]
            return [None if nulls[i] else raw[i] for i in range(self._rows)]
        raw = c["strs"]
        return [None if nulls[i] else raw[i] for i in range(self._rows)]

    def to_dicts(self) -> list:
        """The rows as dicts keyed by the spec's leaf names (None at
        null slots). Requires the plan's spec to have named children."""
        if not self._names:
            raise YeptrisError(
                "the plan spec carries no named children: to_dicts needs "
                '{"name": ...} entries under "children"')
        if len(self._names) != len(self._columns):
            raise YeptrisError(
                "the plan's column count (%d) does not match its named "
                "children (%d)" % (len(self._columns), len(self._names)))
        cols = [self.column(c) for c in range(len(self._columns))]
        return [dict(zip(self._names, values)) for values in zip(*cols)]


def _collect(r, ncols: int, *, source, names) -> PlanResult:
    """Copy the whole C result into Python, then free it — the caller
    hands out only owned data."""
    try:
        rows = F._lib.yeptris_plan_result_rows(r)
        kinds = []
        columns = []
        for c in range(ncols):
            k = F._lib.yeptris_plan_result_kind(r, c)
            name = _KIND_NAMES.get(k)
            if name is None:
                raise YeptrisError("unknown plan column kind %d" % k)
            kinds.append(name)
            nulls = (ctypes.string_at(F._lib.yeptris_plan_result_nulls(r, c), rows)
                     if rows else b"")
            ints = floats = None
            strs = []
            if name in ("int", "bool"):
                p = F._lib.yeptris_plan_result_ints(r, c)
                ints = (array.array("q", ctypes.string_at(p, 8 * rows))
                        if rows else array.array("q"))
            elif name == "float":
                p = F._lib.yeptris_plan_result_floats(r, c)
                floats = (array.array("d", ctypes.string_at(p, 8 * rows))
                          if rows else array.array("d"))
            else:  # str
                if source is not None:
                    # the tape leg: spans into the tape's source
                    offs = F._lib.yeptris_plan_result_str_offs(r, c)
                    lens = F._lib.yeptris_plan_result_str_lens(r, c)
                    strs = [
                        source[offs[i]:offs[i] + lens[i]].decode("utf-8")
                        for i in range(rows)]
                else:
                    # the DOM leg: (ptr,len) views into the document.
                    # THE NULL-SLOT LAW: the engine leaves the cell for
                    # a null-marked row UNSPECIFIED (plan.h — "missing
                    # or null leaves mark the slot null"; the typed
                    # slot carries garbage), so the bitmap gates every
                    # cell read — never touch cells[i] for a null row
                    # (glibc hands back dirty heap where macOS
                    # zero-fills: the segfault the CI leg caught)
                    sp = F._lib.yeptris_plan_result_strs(r, c)
                    if not sp:
                        strs = []
                    else:
                        cells = ctypes.cast(
                            sp, ctypes.POINTER(F._PlanStr * rows)).contents
                        strs = []
                        for i in range(rows):
                            if nulls[i]:
                                strs.append("")
                                continue
                            p = cells[i].p
                            strs.append(
                                ctypes.string_at(p, cells[i].len).decode(
                                    "utf-8") if p else "")
            columns.append({"ints": ints, "floats": floats,
                            "strs": strs, "nulls": nulls})
        return PlanResult(rows, kinds, columns, names)
    finally:
        F._lib.yeptris_plan_result_free(r)
