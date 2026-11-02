"""The composer stage: an alias no anchor in the document resolves
(``bad: *missing`` — PyYAML's ComposerError).
"""

from .._errors import ComposerError  # noqa: F401

__all__ = ["ComposerError"]
