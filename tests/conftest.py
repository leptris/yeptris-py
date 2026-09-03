import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

lib = Path(__file__).resolve().parents[2] / "yeptris"
for build in ("build-validate", "build"):
    for name in ("libyeptris.dylib", "libyeptris.so"):
        cand = lib / build / "src" / name
        if cand.exists():
            os.environ.setdefault("YEPTRIS_LIB_PATH", str(cand))
            break
