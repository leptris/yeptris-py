"""The #209 public surface: the compiled `Schema` class is reachable
without a private-module import — at package level and via the public
`yeptris.schema` submodule.

Pure re-export law: the published names are the SAME objects the
engine-backed `yeptris._validate` defines (no wrapper, no
re-serialization anywhere). These checks need no engine face — the
re-export must hold regardless of the loaded libyeptris's age; the
behavioral economy itself is pinned in test_validate.py.
"""

import yeptris
from yeptris import _validate as impl
from yeptris import schema as public


def test_schema_importable_at_package_level():
    assert yeptris.Schema is impl.Schema
    assert "Schema" in yeptris.__all__


def test_schema_importable_via_public_submodule():
    from yeptris.schema import Schema

    assert Schema is impl.Schema


def test_submodule_reexports_the_whole_face_verbatim():
    # the public door carries the compiled economy, not just the class
    assert public.Schema is impl.Schema
    assert public.compile_schema is impl.compile_schema
    assert public.validate is impl.validate
    assert public.is_valid is impl.is_valid
    assert public.Diagnostic is impl.Diagnostic
    assert public.available is impl.available
    assert set(public.__all__) == set(impl.__all__)


def test_compiled_schema_constructs_through_the_public_door():
    schema_doc = '{"type": "object", "required": ["name"]}'
    if not public.available():
        # an engine older than the validation face (C 0.6.59): the
        # re-export still holds; construction needs the newer engine
        # (pinned under the face-available gate in test_validate.py)
        return
    schema = public.compile_schema(schema_doc)
    try:
        assert isinstance(schema, yeptris.Schema)
        assert schema.is_valid(b"name: x\n")
        assert not schema.is_valid(b"age: 1\n")
    finally:
        schema.close()
