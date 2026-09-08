"""yeptris — YAML for Python at libleptris speed.

An FFI-based (no C extension) YAML library over libyeptris. The
neutral surface lives here; `yeptris.yaml` carries the PyYAML-
compatible one.
"""

from ._ffi import ParseError, YeptrisError
from ._dumper import dump
from ._loader import load, load_all

try:  # pyproject.toml is the version SSOT; the dev tree (not
    # installed) falls back to reading it directly
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("yeptris")
    except PackageNotFoundError:
        import re

        __version__ = re.search(
            r'^version = "(.+)"',
            __import__("pathlib").Path(__file__).resolve().parent.parent.joinpath(
                "pyproject.toml").read_text(),
            re.M,
        ).group(1)
except Exception:  # pragma: no cover - metadata always present when installed
    __version__ = "0.0.0"

__all__ = [
    "load", "load_all", "dump",
    "YeptrisError", "ParseError", "__version__",
]
