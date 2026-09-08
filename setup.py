"""Optional native build (TODO.restructure/41): the fused JSON
materializer extension, linked against libyeptris.

Mirrors yeptris-ruby's extconf.rb conventions: YEPTRIS_LIB_PATH (the
shared library, file or its directory) + YEPTRIS_SRC (the C checkout)
drive includes/linkage; sibling-checkout fallbacks serve development.
Without them the package builds PURE — the no-C-extension contract is
the default install, the extension is the opt-in performance path
(feature-detected at import, ctypes fallback behind it).
"""

import os
import sys
from pathlib import Path

from setuptools import Extension, setup

_PY = sys.version_info >= (3, 9)


def _src_roots():
    env = os.environ.get("YEPTRIS_SRC")
    if env:
        yield Path(env)
    here = Path(__file__).resolve().parent
    yield here.parent / "yeptris" / "src"


def _lib_dirs():
    env = os.environ.get("YEPTRIS_LIB_PATH")
    if env:
        p = Path(env)
        yield p if p.is_dir() else p.parent
    here = Path(__file__).resolve().parent
    for build in ("build-shared", "build", "build-validate"):
        yield here.parent / "yeptris" / build / "src"


def _native_ext():
    src_root = next((r for r in _src_roots() if (r / "include").is_dir()), None)
    lib_dir = next((d for d in _lib_dirs()
                    if d.is_dir() and any(d.glob("libyeptris.*"))), None)
    if src_root is None or lib_dir is None:
        return None

    include_dirs = [str(src_root / "include"), str(src_root / "yeptris")]
    for gen in ("build-shared/generated", "build/generated"):
        g = src_root.parent / gen
        if g.is_dir():
            include_dirs.append(str(g))

    kwargs = {"runtime_library_dirs": [str(lib_dir)]} if sys.platform != "darwin" else {
        "extra_link_args": [f"-Wl,-rpath,{lib_dir}"],
    }
    return Extension(
        "yeptris._native",
        ["ext/yeptris_native.c"],
        include_dirs=include_dirs,
        library_dirs=[str(lib_dir)],
        libraries=["yeptris"],
        extra_compile_args=["-O3"],
        **kwargs,
    )


ext = _native_ext()
if os.environ.get("YEPTRIS_NATIVE_BUILD", "") == "1" and ext is None:
    sys.exit("YEPTRIS_NATIVE_BUILD=1 but libyeptris was not found "
             "(set YEPTRIS_LIB_PATH and YEPTRIS_SRC)")

setup(ext_modules=[ext] if ext is not None else [])
