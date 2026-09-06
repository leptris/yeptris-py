

def test_input_typing_all_paths():
    import io as _io
    import pytest
    from yeptris import load, _loader

    doc = "k: v\n"
    assert load(doc) == {"k": "v"}
    assert load(doc.encode()) == {"k": "v"}
    assert load(bytearray(doc.encode())) == {"k": "v"}
    assert load(_io.BytesIO(doc.encode())) == {"k": "v"}
    assert load(_io.StringIO(doc)) == {"k": "v"}
    with open(__file__, "rb") as f:
        head = f.read(64)
    with pytest.raises(TypeError):
        _loader._as_bytes(object())
