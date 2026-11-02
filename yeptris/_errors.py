"""The exception taxonomy: ONE definition module, imported everywhere.

The graph mirrors PyYAML's (yaml.error + the stage modules): every
stage error is catchable as ``yaml.YAMLError`` the PyYAML way, and
each stage keeps its own import path (``yeptris.yaml.parser`` ->
``ParserError`` and so on — see the ``yeptris/yaml/`` package).

Backward compatibility is carried in the hierarchy itself, not at the
raise sites: every load-stage error subclasses the legacy
``ParseError`` (pre-taxonomy code catching ``yeptris.ParseError``
keeps working unchanged), and ``RepresenterError`` subclasses
``TypeError`` (the dumper's pre-taxonomy contract). ``YeptrisError``
stays the root of the whole binding, as before.
"""


class YeptrisError(Exception):
    """Base: something went wrong inside or around the library."""


class YAMLError(YeptrisError):
    """PyYAML-compatible base: every stage error below catches as
    this (``except yaml.YAMLError`` keeps working)."""


class MarkedYAMLError(YAMLError):
    """A stage error carrying the 1-based source mark. The mark rides
    the message PyYAML-style (``... in line 1, column 3``) and stays
    addressable as ``.line`` / ``.column`` (0 when the drain that
    raised carried no marks — the columnar walk has none)."""

    def __init__(self, message="", line=0, column=0):
        if line:
            message = f"{message} in line {line}, column {column}"
        super().__init__(message)
        self.line = line
        self.column = column


class ParseError(MarkedYAMLError):
    """The legacy load-side error (formerly YeptrisError's direct
    child): every load-stage error below ALSO catches as this."""


class ReaderError(ParseError):
    """The input could not be decoded (engine ERROR_ENCODING)."""


class ScannerError(ParseError):
    """Reserved: the engine fuses scanning into its single parse
    stage, so scan-stage rejections surface as ParserError today.
    Exported for the PyYAML import graph; raised the day a scan-stage
    condition becomes binding-detectable."""


class ParserError(ParseError):
    """Malformed YAML (the engine's parse-stage status)."""


class ComposerError(ParseError):
    """Composition failed: an alias no anchor in the document
    resolves (PyYAML's composer rejects the load the same way)."""


class ConstructorError(ParseError):
    """Construction failed: a merge value that is not a mapping (or
    list of mappings), or an explicitly-tagged scalar the constructor
    cannot build (``!!int xyz``)."""


class RepresenterError(YAMLError, TypeError):
    """An object has no safe representation (dump side). Also a
    ``TypeError``: the dumper's pre-taxonomy contract, which existing
    code catches."""


class EmitterError(YAMLError):
    """The engine failed while serializing a document (dump side)."""
