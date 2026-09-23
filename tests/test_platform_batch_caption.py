from app.editorial import format_video_caption
from app.platform_batch import _file_fingerprint


def test_platform_caption_uses_metadata_from_each_video():
    first = format_video_caption(
        "Primeiro vídeo",
        {
            "person": "Pessoa Um",
            "categories": ["Categoria A"],
            "tags": ["tag um", "tag dois"],
        },
    )
    second = format_video_caption(
        "Segundo vídeo",
        {
            "person": "Pessoa Dois",
            "categories": ["Categoria B"],
            "tags": ["tag três"],
        },
    )

    assert "🚫 Pessoa Um" in first
    assert "#Categoria_A" in first
    assert "#Tag_Um" in first
    assert "#Tag_Dois" in first

    assert "🚫 Pessoa Dois" in second
    assert "#Categoria_B" in second
    assert "#Tag_Três" in second
    assert first != second


def test_file_fingerprint_matches_identical_payloads(tmp_path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    third = tmp_path / "c.mp4"
    first.write_bytes(b"a" * 2_000_000 + b"z" * 64)
    second.write_bytes(first.read_bytes())
    third.write_bytes(b"b" * 2_000_000 + b"z" * 64)

    assert _file_fingerprint(first) == _file_fingerprint(second)
    assert _file_fingerprint(first) != _file_fingerprint(third)
