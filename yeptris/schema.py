"""The validation face's public door (#209): the compiled JSON-Schema
economy — parse the schema ONCE, validate MANY streams — plus the
schema-family walk and the structured diagnostics.

This module re-exports the engine-backed implementation in
:mod:`yeptris._validate`; the names are the same objects, published
here so consumers never import a private path:

    from yeptris.schema import Schema

    schema = Schema(schema_document)   # compile once
    for stream in streams:
        diags = schema.validate(stream)   # ... validate many

`Schema` is also re-exported at package level (`from yeptris import
Schema`). See :mod:`yeptris._validate` for the full contract: the
`Diagnostic` shape, the keyword taxonomy, the whole-report law, and
the thread law (one handle per thread; immutable after compile, so
read-only sharing is safe).
"""

from ._validate import (  # noqa: F401 — the face, re-exported verbatim
    Diagnostic,
    Schema,
    available,
    compile_schema,
    is_valid,
    validate,
)

__all__ = ["Diagnostic", "Schema", "available", "compile_schema",
           "is_valid", "validate"]
