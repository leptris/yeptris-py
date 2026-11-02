"""The validation face (engine TODO.validate.md item 6): the JSON
Schema 2020-12 walk, the schema-family walk, diagnostics as
Diagnostic dataclasses (never a c_void_p), the compiled-schema
economy, and the load() keyword forms."""

import ctypes
import io

import pytest

import yeptris
from yeptris import _ffi as F
from yeptris import yaml as pyml
from yeptris.yaml import (Diagnostic, Schema, ValidationError, YAMLError,
                          compile_schema, is_valid, validate)

pytestmark = pytest.mark.skipif(
    not F.VALIDATE_AVAILABLE,
    reason="the vendored libyeptris predates the validation face (C 0.6.59)")

# schema: an object with a required string and an integer-array field
SCHEMA = (
    '{"type": "object", "required": ["name"], "properties": {'
    '"name": {"type": "string"},'
    '"tags": {"type": "array", "items": {"type": "integer"}}}}'
)


# --- the JSON Schema walk ------------------------------------------------

def test_valid_document_answers_zero_diagnostics():
    assert validate(b"name: yeptris\ntags: [1, 2]\n", json_schema=SCHEMA) == []
    assert is_valid(b"name: yeptris\n", json_schema=SCHEMA)
    # str input rides the same door
    assert validate("name: yeptris\n", json_schema=SCHEMA) == []


def test_invalid_document_pins_every_diag_field():
    (d,) = validate(b"age: 5\n", json_schema=SCHEMA)
    assert isinstance(d, Diagnostic)
    # a structured object, never a c_void_p
    assert not isinstance(d, ctypes._Pointer)
    assert type(d.code) is int and type(d.line) is int
    assert d.code == 6  # YEPTRIS_DIAG_REQUIRED
    assert d.keyword == "required"
    assert d.path == ["name"]
    assert d.pointer == "/name"
    assert d.message == 'missing required property "name"'
    assert (d.line, d.col) == (1, 1)


def test_nested_type_miss_carries_the_pointer_path():
    (d,) = validate(b'name: [1, "two"]\n', json_schema=SCHEMA)
    assert d.code == 5  # YEPTRIS_DIAG_TYPE
    assert d.keyword == "type"
    assert d.path == ["name"]
    assert d.message == "expected string, got array"
    assert (d.line, d.col) == (1, 8)


def test_array_items_carry_indexed_paths():
    (d,) = validate(b'name: x\ntags: [1, "oops"]\n', json_schema=SCHEMA)
    assert d.keyword == "type"
    assert d.path == ["tags", "1"]
    assert d.pointer == "/tags/1"


def test_the_whole_report_enumerates_every_miss():
    # the required miss AND the items miss, in one report
    diags = validate(b'tags: ["x"]\n', json_schema=SCHEMA)
    assert [(d.keyword, d.path) for d in diags] == [
        ("required", ["name"]), ("type", ["tags", "0"])]


def test_multi_document_stream_validates_every_document():
    diags = validate(b"name: a\n---\nage: 1\n", json_schema=SCHEMA)
    assert [(d.keyword, d.path) for d in diags] == [("required", ["name"])]
    assert diags[0].line == 3  # the mark points into document 2


def test_schema_accepts_str_bytes_and_compiled_handles():
    for form in (SCHEMA, SCHEMA.encode()):
        assert validate(b"name: x\n", json_schema=form) == []
    schema = compile_schema(SCHEMA)
    assert schema.is_valid(b"name: x\n")
    assert schema.validate(b"other: 2\n")[0].keyword == "required"
    # the compiled economy: one compile, many streams
    assert schema.is_valid(b"name: y\n")
    assert not schema.is_valid(b"name: 5\n")


def test_compiled_schema_is_a_context_manager_and_close_is_garded():
    with compile_schema(SCHEMA) as schema:
        assert isinstance(schema, Schema)
        assert schema.validate(b"name: x\n") == []
    with pytest.raises(yeptris.YeptrisError):
        schema.validate(b"name: x\n")
    # the closed handle is rejected on the module-level door too
    with pytest.raises(yeptris.YeptrisError):
        validate(b"name: x\n", json_schema=schema)
    schema.close()  # idempotent


def test_invalid_schema_bytes_raise_the_load_taxonomy():
    # a schema that does not parse: the parse stage, PyYAML-catchable
    with pytest.raises(pyml.ParserError):
        validate(b"a: 1\n", json_schema=b"{bad")
    # bytes that hold no document: ERROR_ARG, the legacy ParseError
    with pytest.raises(yeptris.ParseError):
        validate(b"a: 1\n", json_schema=b"")
    for bad in (b"{bad", b""):
        with pytest.raises(YAMLError):
            validate(b"a: 1\n", json_schema=bad)
    with pytest.raises(TypeError):
        validate(b"a: 1\n", json_schema=123)


def test_local_ref_resolves_through_defs():
    schema = ('{"$defs": {"pos": {"type": "integer", "minimum": 1}},'
              '"properties": {"n": {"$ref": "#/$defs/pos"}}}')
    assert validate(b"n: 5\n", json_schema=schema) == []
    (d,) = validate(b"n: 0\n", json_schema=schema)
    assert d.keyword == "minimum"
    # an unresolvable $ref is a diagnostic, not an exception
    (d,) = validate(b"n: 1\n", json_schema='{"$ref": "#/$defs/missing"}')
    assert d.keyword == "$ref"


def test_file_like_input_rides_the_same_door():
    assert validate(io.BytesIO(b"name: x\n"), json_schema=SCHEMA) == []
    assert is_valid(io.BytesIO(b"age: 1\n"), json_schema=SCHEMA) is False


# --- the schema-family walk ----------------------------------------------

def test_core_walk_flags_duplicate_keys_with_marks():
    (d,) = validate(b"a: 1\na: 2\n")
    assert d.code == 4  # YEPTRIS_DIAG_DUPLICATE_KEY
    assert d.keyword == "duplicateKey"
    assert d.path == []
    assert d.message == 'duplicate mapping key ("a")'
    assert (d.line, d.col) == (2, 1)


def test_core_walk_keys_compare_by_canonical_form():
    # spec 3.2.1.3: 0o13 and 0xB are the same key
    (d,) = validate(b"0o13: 1\n0xB: 2\n")
    assert d.keyword == "duplicateKey"
    assert d.message == 'duplicate mapping key ("0xB")'


def test_core_walk_flags_contradictory_explicit_tags():
    (d,) = validate(b"v: !!bool 3\n")
    assert d.code == 1  # YEPTRIS_DIAG_TAG_MISMATCH
    assert d.keyword == "tag"
    assert d.path == ["v"]
    assert d.message == "explicit bool tag on int content"
    assert (d.line, d.col) == (1, 11)


def test_valid_core_document_answers_zero_diagnostics():
    assert validate(b"name: x\nnums: [1, 2.5, ~]\n") == []
    assert is_valid(b"a: 1\nb: {c: [2]}\n")


def test_json_family_walk_rejects_non_json_productions():
    diags = validate(b"a: yes\n", schema=F.SCHEMA_12_JSON)
    assert any(d.keyword == "jsonType" and d.path == ["a"] for d in diags)


def test_json_family_walk_requires_string_keys():
    diags = validate(b"1: x\n", schema=F.SCHEMA_12_JSON)
    (d,) = [x for x in diags if x.keyword == "keyType"]
    assert d.code == 2  # YEPTRIS_DIAG_KEY_NOT_STRING
    assert d.path == []
    assert d.message == "mapping key is int (expected a string)"
    # the same int key is also a non-JSON plain scalar at its path
    assert any(x.keyword == "jsonType" and x.path == ["1"] for x in diags)


# --- the load() keyword forms ---------------------------------------------

def test_load_json_schema_raises_validation_error():
    with pytest.raises(ValidationError) as ei:
        pyml.safe_load(b"age: 5\n", json_schema=SCHEMA)
    assert len(ei.value.diagnostics) == 1
    assert ei.value.diagnostics[0].keyword == "required"
    # the input PARSED: ValidationError is a YAMLError, not a ParseError
    assert isinstance(ei.value, YAMLError)
    assert not isinstance(ei.value, yeptris.ParseError)
    with pytest.raises(yeptris.YAMLError):
        pyml.safe_load(b"age: 5\n", json_schema=SCHEMA)


def test_load_json_schema_collect_mode():
    doc, diags = pyml.safe_load(b"age: 5\n", json_schema=SCHEMA,
                                collect=True)
    assert doc == {"age": 5}
    assert [d.keyword for d in diags] == ["required"]
    # valid streams answer (document, [])
    assert pyml.safe_load(b"name: x\n", json_schema=SCHEMA,
                          collect=True) == ({"name": "x"}, [])


def test_load_validate_true_rides_the_family_walk():
    with pytest.raises(ValidationError) as ei:
        pyml.safe_load(b"a: 1\na: 2\n", validate=True)
    assert ei.value.diagnostics[0].keyword == "duplicateKey"
    assert pyml.safe_load(b"a: 1\n", validate=True) == {"a": 1}


def test_collect_without_a_validation_request_is_a_caller_error():
    with pytest.raises(TypeError):
        pyml.safe_load(b"a: 1\n", collect=True)


def test_load_compiled_schema_handle():
    schema = compile_schema(SCHEMA)
    assert pyml.safe_load(b"name: x\n", json_schema=schema) == {"name": "x"}
    with pytest.raises(ValidationError):
        pyml.safe_load(b"age: 1\n", json_schema=schema)


def test_load_all_accepts_the_same_forms():
    # the laziness law holds: construction never validates, the raise
    # waits for iteration
    gen = pyml.safe_load_all(b"name: a\n---\nage: 1\n", json_schema=SCHEMA)
    with pytest.raises(ValidationError):
        list(gen)
    docs = pyml.safe_load_all(b"name: a\n---\nname: b\n", json_schema=SCHEMA)
    assert list(docs) == [{"name": "a"}, {"name": "b"}]
    with pytest.raises(ValidationError):
        list(pyml.safe_load_all(b"a: 1\na: 2\n", validate=True))
