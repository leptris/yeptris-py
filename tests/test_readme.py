"""The README's example blocks are the SSOT — these tests execute
them verbatim so docs cannot rot (the Ruby binding's spec/readme_spec
is the mirror)."""
import re
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"

BLOCKS = re.findall(r"```python\n(.*?)```", README.read_text(), re.S)
assert BLOCKS, "no python blocks found in README.md"


def test_readme_has_python_examples():
    assert len(BLOCKS) >= 1


def _run(block: str) -> None:
    exec(compile(block, "README.md python block", "exec"), {})


def test_readme_examples_run():
    for i, block in enumerate(BLOCKS):
        _run(block)
