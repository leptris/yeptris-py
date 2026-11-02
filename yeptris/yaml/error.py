"""The taxonomy root, PyYAML-style: YAMLError and its marked
variant. Defined once in yeptris._errors; every stage module
re-exports from there (one definition, many import paths).
"""

from .._errors import MarkedYAMLError, YAMLError  # noqa: F401

__all__ = ["YAMLError", "MarkedYAMLError"]
