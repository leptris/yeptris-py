"""The capability gate's CLOSED side (py#171): on a libyeptris that
predates the stream iterator's slice accessor, `each_document` is
inert BY DESIGN — the call stays lazy (the load_all law holds even
before the face exists: nothing probes the engine until iteration)
and the first iteration raises the ImportError naming the fix.
Runs exactly where tests/test_each_document.py (the open-gate walk)
skips: one live side per engine, never both, never neither."""

import pytest

from yeptris import _ffi as F
from yeptris import yaml

pytestmark = pytest.mark.skipif(
    F.STREAM_ITER_AVAILABLE and F.STREAM_FRAMES_AVAILABLE,
    reason="the loaded libyeptris carries the slice accessor: "
           "the walk tests own the face")


def test_gate_closed_raises_import_error_at_first_iteration():
    # the probes themselves are the gate (defense against mark drift)
    assert not (F.STREAM_ITER_AVAILABLE and F.STREAM_FRAMES_AVAILABLE)
    gen = yaml.each_document("--- 1\n--- 2\n")
    # the call itself never raises and never touches the engine: a
    # generator, per the laziness law
    assert iter(gen) is gen
    with pytest.raises(ImportError) as ei:
        list(gen)
    # the message names the missing capability AND the fix
    assert "slice accessor" in str(ei.value)
    assert "YEPTRIS_LIB_PATH" in str(ei.value)
