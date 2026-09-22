from zipfile import ZipFile

from app.delivery import build_zip, human_bytes


def test_human_bytes():
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(1024 * 1024) == "1.0 MB"


def test_build_zip(tmp_path):
    a = tmp_path / "001.jpg"
    b = tmp_path / "002.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    out = build_zip([a, b], tmp_path / "chapter.zip")
    with ZipFile(out) as zf:
        assert zf.namelist() == ["001.jpg", "002.jpg"]
