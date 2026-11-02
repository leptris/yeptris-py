"""The YAMLError taxonomy: PyYAML's import graph and stage classes,
with the legacy binding classes (ParseError / TypeError) carried in
the hierarchy itself so pre-taxonomy catch sites keep working."""

import pytest
import yaml as pyyaml

import yeptris
from yeptris import yaml as pyml
from yeptris.yaml.error import MarkedYAMLError, YAMLError
from yeptris.yaml.parser import ParserError
from yeptris.yaml.composer import ComposerError
from yeptris.yaml.constructor import ConstructorError
from yeptris.yaml.representer import RepresenterError
from yeptris.yaml.reader import ReaderError
from yeptris.yaml.emitter import EmitterError
from yeptris.yaml.scanner import ScannerError


def test_import_graph_mirrors_pyyaml():
    # `import yaml.parser` must work the way it does in PyYAML: every
    # stage module imports and carries its own error class
    import yeptris.yaml.constructor
    import yeptris.yaml.emitter
    import yeptris.yaml.error
    import yeptris.yaml.parser
    import yeptris.yaml.reader
    import yeptris.yaml.representer
    import yeptris.yaml.scanner

    assert yeptris.yaml.parser.ParserError is ParserError
    assert yeptris.yaml.composer.ComposerError is ComposerError
    assert yeptris.yaml.constructor.ConstructorError is ConstructorError
    assert yeptris.yaml.representer.RepresenterError is RepresenterError
    assert yeptris.yaml.scanner.ScannerError is ScannerError
    assert yeptris.yaml.error.YAMLError is YAMLError
    # the classes are defined ONCE (SSOT) and aliased everywhere
    assert ParserError.__module__ == "yeptris._errors"


def test_parse_error_is_parser_error_with_mark():
    # differential: PyYAML's parser stage raises ParserError for the
    # same input; ours carries the mark in the message PyYAML-style
    with pytest.raises(ParserError) as ei:
        pyml.safe_load("a: [1, 2")
    assert ei.value.line == 1
    assert ei.value.column >= 1
    assert "in line 1" in str(ei.value)
    with pytest.raises(pyyaml.parser.ParserError):
        pyyaml.safe_load("a: [1, 2")


def test_parse_error_mark_style():
    # the mark rides the message the PyYAML way ("in line 1, column 3")
    with pytest.raises(ParserError) as ei:
        pyml.safe_load("a: 1\nb: [")
    assert "in line 2" in str(ei.value)


def test_depth_limit_is_parser_error():
    # engine ERROR_DEPTH is a parse-stage rejection
    with pytest.raises(ParserError):
        pyml.safe_load("[" * 2000 + "]" * 2000)


def test_encoding_error_is_reader_error():
    # engine ERROR_ENCODING -> the reader stage
    with pytest.raises(ReaderError):
        pyml.safe_load(b"\xff\xfe")


def test_undefined_alias_is_composer_error():
    with pytest.raises(ComposerError) as ei:
        pyml.safe_load("bad: *missing")
    assert "found undefined alias 'missing'" in str(ei.value)
    assert ei.value.line == 1  # the record walk carries marks
    # the record walk (the non-columnar fallback) raises the same class
    with pytest.raises(ComposerError):
        yeptris._loader.load_all(b"bad: *missing")


def test_constructor_failure_is_constructor_error():
    # explicitly-tagged scalars the constructor cannot build; both
    # walks reject (PyYAML's SafeLoader fails the same inputs, letting
    # the raw int()/lookup error escape — ConstructorError is the
    # YAMLError-catchable form this binding raises instead)
    for text in ["a: !!int xyz", "a: !!float zz", "a: !!bool notabool"]:
        with pytest.raises(ConstructorError):
            pyml.safe_load(text)
        with pytest.raises(ConstructorError):
            yeptris._loader.load_all(text)
    # valid explicit tags still construct
    assert pyml.safe_load("a: !!int 12") == {"a": 12}
    assert yeptris._loader.load_all_columns("a: !!int 12") == [{"a": 12}]


def test_dump_unrepresentable_is_representer_error():
    with pytest.raises(RepresenterError) as ei:
        pyml.safe_dump(object())
    assert "cannot dump" in str(ei.value)
    # PyYAML's representer stage raises its RepresenterError too
    with pytest.raises(pyyaml.representer.RepresenterError):
        pyyaml.safe_dump(object())


def test_representer_error_is_still_a_type_error():
    # the dumper's pre-taxonomy contract: `except TypeError` keeps
    # working (pinned in test_dump.py as well)
    with pytest.raises(TypeError):
        pyml.safe_dump(object())


def test_everything_catches_as_yaml_error():
    cases = [
        (pyml.safe_load, ("a: [1, 2",), ParserError),
        (pyml.safe_load, ("bad: *missing",), ComposerError),
        (pyml.safe_load, ("a: !!int xyz",), ConstructorError),
        (pyml.safe_dump, (object(),), RepresenterError),
    ]
    for func, args, stage in cases:
        with pytest.raises(YAMLError):
            func(*args)
        with pytest.raises(stage):
            func(*args)


def test_legacy_parse_error_still_catches_everything():
    # compat law: every load-stage error also catches as the legacy
    # yeptris.ParseError, and everything as yeptris.YeptrisError
    for text in ["a: [1, 2", "bad: *missing", "a: !!int xyz", "<<: 5"]:
        with pytest.raises(yeptris.ParseError):
            pyml.safe_load(text)
        with pytest.raises(yeptris.YeptrisError):
            pyml.safe_load(text)


def test_marked_errors_carry_line_and_column_attributes():
    with pytest.raises(MarkedYAMLError) as ei:
        pyml.safe_load("a: [1, 2")
    assert ei.value.line == 1 and ei.value.column >= 1


def test_scanner_error_is_importable_and_a_yaml_error():
    # reserved class: the engine fuses scanning into its parse stage,
    # so it stays unraised today — but the PyYAML import graph is whole
    assert issubclass(ScannerError, YAMLError)
    assert issubclass(ScannerError, yeptris.ParseError)
