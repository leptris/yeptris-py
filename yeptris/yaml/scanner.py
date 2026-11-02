"""The scanner stage. The engine fuses scanning into its single
parse status, so scan-stage rejections surface as ParserError
today; this class keeps the PyYAML import graph whole and is
raised the day a scan-stage condition becomes
binding-detectable (see yeptris._errors).
"""

from .._errors import ScannerError  # noqa: F401

__all__ = ["ScannerError"]
