"""The README's example blocks are the SSOT — these tests execute
them verbatim so docs cannot rot (the Ruby binding's spec/readme_spec
is the mirror)."""
import re
from pathlib import Path

from yeptris import _ffi as F

README = Path(__file__).parent.parent / "README.md"

BLOCKS = re.findall(r"```python\n(.*?)```", README.read_text(), re.S)
assert BLOCKS, "no python blocks found in README.md"


def test_readme_has_python_examples():
    assert len(BLOCKS) >= 1


def _run(block: str) -> None:
    # a block may declare its engine floor in a leading comment
    # ('# needs libyeptris >= X.Y.Z'): the import-time floor law, per
    # block — a face the loaded engine predates skips here exactly
    # where the module code feature-detects (the encoding pins'
    # vintage split, README edition)
    m = re.search(r"^# needs libyeptris >= (\d+)\.(\d+)\.(\d+)", block,
                  re.M)
    if m and F._version_tuple(F._ENGINE_VERSION) < tuple(map(int,
                                                             m.groups())):
        import pytest

        pytest.skip("needs libyeptris >= %s" % ".".join(m.groups()))
    exec(compile(block, "README.md python block", "exec"), {})


def test_readme_examples_run():
    for i, block in enumerate(BLOCKS):
        _run(block)
