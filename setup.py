"""Optional native build (TODO.restructure/41/42): the fused JSON
materializer extension, linked against libyeptris.

Mirrors yeptris-ruby's extconf.rb conventions: YEPTRIS_LIB_PATH (the
shared library, file or its directory) + YEPTRIS_SRC (the C checkout)
drive includes/linkage; sibling-checkout fallbacks serve development.
Without them the package builds PURE — the no-C-extension contract is
the default install, the extension is the opt-in performance path
(feature-detected at import, ctypes fallback behind it).

WHEEL VENDORING (YEPTRIS_VENDOR=1, what the platform wheels set):
the extension is built against Py_LIMITED_API 0x03090000 (abi3 — one
wheel per platform serves CPython 3.9+) and the shared libyeptris is
COPIED into yeptris/_platform/<version>/ — the location the ctypes
ladder already searches — with a RELATIVE rpath from the extension
(@loader_path on macOS, $ORIGIN on Linux; never an absolute build
path, the platform-gem lesson). auditwheel repair on Linux may
relocate the lib into yeptris.libs/ — the wheel smoke gate proves
the final artifact loads either way.
"""

import os
import shutil
import sys
from pathlib import Path

from setuptools import Extension, setup

_PY39 = sys.version_info >= (3, 9)


def _src_roots():
    env = os.environ.get("YEPTRIS_SRC")
    if env:
        yield Path(env)
    here = Path(__file__).resolve().parent
    yield here / "vendor" / "libyeptris" / "src"
    yield here.parent / "yeptris" / "src"


def _lib_dirs():
    env = os.environ.get("YEPTRIS_LIB_PATH")
    if env:
        p = Path(env)
        yield p if p.is_dir() else p.parent
    here = Path(__file__).resolve().parent
    for build in ("build-shared", "build", "build-validate"):
        yield here.parent / "yeptris" / build / "src"


def _lib_file():
    # the MSVC build names the DLL yeptris.dll (and stages under
    # bin/Release); both spellings are probed wherever they may sit
    names = ("libyeptris.dylib", "libyeptris.so", "yeptris.dll", "libyeptris.dll")
    return next((d / n for d in _lib_dirs() for n in names if (d / n).exists()), None)


def _implib_dirs(lib_file: Path):
    # MSVC links the ext against the IMPORT library (yeptris.lib), not
    # the DLL — CMake's VS generator stages it away from the DLL
    # (build/src/Release while the DLL is in build/bin/Release), so
    # search the CMake build dir rather than guessing the offset
    build_root = next((p for p in lib_file.parents if p.name == "build"), None)
    dirs = [lib_file.parent]
    if build_root is not None:
        for cand in sorted(build_root.rglob("yeptris.lib")):
            if cand.parent not in dirs:
                dirs.append(cand.parent)
    return dirs


def _c_version(src_root: Path) -> str:
    text = (src_root.parent / "CMakeLists.txt").read_text()
    for line in text.splitlines():
        if "VERSION " in line and line.strip().lstrip().startswith("VERSION"):
            return line.split("VERSION")[1].strip()
    raise RuntimeError("CMakeLists VERSION not found")


def _build_vendored():
    # libyeptris rides EVERY artifact: when no prebuilt lib matches
    # (pip fell back to the sdist on an uncovered platform), build
    # the vendored C sources into the package's _platform tree
    here = Path(__file__).resolve().parent
    vendored = next(
        (p for p in (here / "vendor" / "libyeptris", here.parent / "yeptris") if (p / "CMakeLists.txt").is_file()),
        None,
    )
    if vendored is None or shutil.which("cmake") is None:
        return None
    import re
    import subprocess
    import tempfile

    m = re.search(r"^\s*VERSION\s+([0-9.]+)", (vendored / "CMakeLists.txt").read_text(), re.M)
    if m is None:
        return None
    ver = m.group(1)
    out_dir = here / "yeptris" / "_platform" / f"v{ver}"
    build = Path(tempfile.mkdtemp(prefix="yeptris-lib-build-"))
    args = [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_TESTING=OFF",
        "-DYEPTRIS_BUILD_CLI=OFF",
        "-DYEPTRIS_BUILD_BENCHMARKS=OFF",
        "-DYEPTRIS_BUILD_SHARED=ON",
    ]
    ok = (
        subprocess.run(["cmake", "-S", str(vendored), "-B", str(build), *args]).returncode == 0
        and subprocess.run(["cmake", "--build", str(build), "--config", "Release"]).returncode == 0
    )
    if not ok:
        return None
    lib = next(build.glob("**/libyeptris.so"), None) or next(build.glob("**/libyeptris.dylib"), None)
    if lib is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / lib.name
    shutil.copy2(lib, dest)
    shutil.rmtree(build, ignore_errors=True)
    return dest


def _native_ext(vendor: bool):
    src_root = next((r for r in _src_roots() if (r / "include").is_dir()), None)
    lib_file = _lib_file()
    if lib_file is None:
        lib_file = _build_vendored()
    if src_root is None or lib_file is None:
        return None

    include_dirs = [str(src_root / "include"), str(src_root / "yeptris")]
    for gen in ("build-shared/generated", "build/generated"):
        g = src_root.parent / gen
        if g.is_dir():
            include_dirs.append(str(g))

    if vendor:
        c_ver = _c_version(src_root)
        vdir = Path(__file__).resolve().parent / "yeptris" / "_platform" / f"v{c_ver}"
        vdir.mkdir(parents=True, exist_ok=True)
        # vendor under the REAL soname name (lib_file may be a symlink
        # to libyeptris.so.0 — the extension's DT_NEEDED is the
        # soname, and auditwheel checks the wheel tree for it); keep
        # the plain name too for the ctypes ladder's glob
        real = lib_file.resolve()
        shutil.copy2(real, vdir / real.name)
        if real.name != lib_file.name:
            shutil.copy2(real, vdir / lib_file.name)
        # Windows: MSVC names the DLL yeptris.dll; the ctypes ladder
        # globs libyeptris.* — vendor under both names
        if real.name == "yeptris.dll":
            shutil.copy2(real, vdir / "libyeptris.dll")
        # the interpreter-independence law (the platform-gem lesson):
        # no absolute rpath, no host-python link — the lib sits inside
        # the package and the extension reaches it relatively
        rel = f"_platform/v{c_ver}"
        if sys.platform == "darwin":
            link = {"extra_link_args": [f"-Wl,-rpath,@loader_path/{rel}"]}
        elif sys.platform == "win32":
            # distutils: "don't know how to set runtime library search
            # path for MSVC" — the loader finds the DLL on PATH
            link = {}
        else:
            link = {"runtime_library_dirs": [f"$ORIGIN/{rel}"]}
        lib_dir, lib_name = str(vdir), "yeptris"
    else:
        lib_dir = str(lib_file.parent)
        if sys.platform == "darwin":
            link = {"extra_link_args": [f"-Wl,-rpath,{lib_dir}"]}
        elif sys.platform == "win32":
            link = {}
        else:
            link = {"runtime_library_dirs": [lib_dir]}
        lib_name = "yeptris"

    # win32 links the IMPORT library (found beside the C build tree);
    # POSIX links the SONAME directly out of the vendored/lib dir
    if sys.platform == "win32":
        link_dirs = [str(d) for d in _implib_dirs(lib_file)]
    else:
        link_dirs = [lib_dir]

    return Extension(
        "yeptris._native",
        ["ext/yeptris_native.c"],
        include_dirs=include_dirs,
        library_dirs=link_dirs,
        libraries=[lib_name],
        extra_compile_args=["-O3"],
        define_macros=[("Py_LIMITED_API", "0x03090000")],
        py_limited_api=True,
        **link,
    )


_vendor = os.environ.get("YEPTRIS_VENDOR", "") == "1"
ext = _native_ext(_vendor)
if os.environ.get("YEPTRIS_NATIVE_BUILD", "") == "1" and ext is None:
    sys.exit("YEPTRIS_NATIVE_BUILD=1 but libyeptris was not found "
             "(set YEPTRIS_LIB_PATH and YEPTRIS_SRC)")

_packages = ["yeptris"]
_platform_root = Path(__file__).resolve().parent / "yeptris" / "_platform"
if _platform_root.is_dir():
    _packages.append("yeptris._platform")
    _packages.extend(f"yeptris._platform.{p.name}" for p in _platform_root.iterdir() if p.is_dir())

setup(
    packages=_packages,
    ext_modules=[ext] if ext is not None else [],
    # the vendored C library rides the wheel on platforms auditwheel
    # does not repair (Windows: nothing else pulls the DLL in — the
    # ctypes ladder globs these paths)
    package_data={"yeptris._platform": ["*/*", "*"]},
)
