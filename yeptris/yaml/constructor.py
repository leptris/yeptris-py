"""The constructor stage: a merge value that is not a mapping
(or list of mappings), or an explicitly-tagged scalar the
constructor cannot build (``!!int xyz``).
"""

from .._errors import ConstructorError  # noqa: F401

__all__ = ["ConstructorError"]
