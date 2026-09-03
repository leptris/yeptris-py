"""yeptris — YAML for Python at libleptris speed.

An FFI-based (no C extension) YAML library over libyeptris. The
neutral surface lives here; `yeptris.yaml` carries the PyYAML-
compatible one.
"""

from ._ffi import ParseError, YeptrisError
from ._dumper import dump
from ._loader import load, load_all

__version__ = "0.1.0"

__all__ = [
    "load", "load_all", "dump",
    "YeptrisError", "ParseError", "__version__",
]
