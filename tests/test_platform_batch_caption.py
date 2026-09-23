from app.platform_batch import _file_fingerprint, _fixed_test_caption


def test_platform_caption_matches_requested_fixed_format():
    caption = _fixed_test_caption(
        "tubepussy",
        {"person": "Ruiva Isabell"},
    )
    assert caption == (
        "<b>🚫 #Ruiva_Isabell</b>\n\n"
        "<blockquote expandable>"
        "🔎 Tags: #Pornô_Longo / #Famosas / #Lésbicas / #Boquetes / #Anal / "
        "#Gostosas / #Novinhas / #Coroas / #Bucetas / #Peitudas / #Mini_Gabys / "
        "#Bundas / #Anã / #Chupando_Buceta / #Gozada_Na_Cara / #Mamando_Rola / "
        "#Pack / #Peitos_Naturais"
        "</blockquote>"
    )


def test_platform_caption_uses_video_person_but_keeps_fixed_tags():
    caption = _fixed_test_caption(
        "xvideosputaria",
        {"person": "Mini Gabys", "tags": ["outra tag"]},
    )
    assert caption.startswith("<b>🚫 #Mini_Gabys</b>")
    assert "#Pornô_Longo" in caption
    assert "#Peitos_Naturais" in caption
    assert "#Outra_Tag" not in caption


def test_file_fingerprint_matches_identical_payloads(tmp_path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    third = tmp_path / "c.mp4"
    first.write_bytes(b"a" * 2_000_000 + b"z" * 64)
    second.write_bytes(first.read_bytes())
    third.write_bytes(b"b" * 2_000_000 + b"z" * 64)

    assert _file_fingerprint(first) == _file_fingerprint(second)
    assert _file_fingerprint(first) != _file_fingerprint(third)
