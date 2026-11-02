"""The representer stage: an object with no safe representation
(dump side). The class subclasses TypeError too — the dumper's
pre-taxonomy contract.
"""

from .._errors import RepresenterError  # noqa: F401

__all__ = ["RepresenterError"]
