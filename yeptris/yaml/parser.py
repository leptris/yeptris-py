"""The parser stage: malformed YAML (the engine's parse-stage
status, which covers its fused scanner).
"""

from .._errors import ParserError  # noqa: F401

__all__ = ["ParserError"]
