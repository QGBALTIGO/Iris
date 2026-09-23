from app.platform_batch import _fixed_test_caption


def test_fixed_platform_caption_matches_requested_format():
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
