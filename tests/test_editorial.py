from app.editorial import extract_editorial_metadata, format_editorial_block, format_video_caption


def test_tubepussy_editorial_metadata():
    html = """
    <html><head>
      <meta property="og:title" content="Ruiva Isabel dando a bucetinha e levando na cara">
      <meta property="og:description" content="Descrição">
    </head><body>
      <div class="tp-video-models-item"><a href="/models/ruiva-isabell/">Ruiva Isabell</a></div>
      <div class="tp-video-categories-item"><a href="/categories/novinha">Novinha</a></div>
      <div class="tp-video-tags-item"><a href="/tags/ruiva/">#ruiva</a></div>
    </body></html>
    """
    meta = extract_editorial_metadata(html, "https://tubepussy.org/video/")
    assert meta.person == "Ruiva Isabell"
    assert meta.categories == ["Novinha"]
    assert meta.tags == ["ruiva"]
    block = format_editorial_block(meta)
    assert "<b>🚫 #Ruiva_Isabell</b>" in block
    assert "#Novinha / #ruiva" in block
    assert "<blockquote expandable>" in block


def test_xvideosputaria_editorial_metadata():
    html = """
    <html><head><title>Teste</title></head><body>
      <div class="pornstar-box"><a href="/pornstar/mini-gabys/">Mini Gabys</a></div>
      <div class="categories">
        <a href="/category/boquetes/">Boquetes</a>
        <a href="/category/bucetas/">Bucetas</a>
        <a href="/category/bundas/">Bundas</a>
        <a href="/category/gostosas/">Gostosas</a>
        <a href="/category/porno-longo/">Pornô Longo</a>
      </div>
      <div class="tags">
        <a href="/tag/bunda-grande/">bunda grande</a>
        <a href="/tag/mamando-rola/">mamando rola</a>
        <a href="/tag/pack/">pack</a>
        <a href="/tag/sexo-ao-ar-livre/">sexo ao ar livre</a>
      </div>
    </body></html>
    """
    meta = extract_editorial_metadata(html, "https://xvideosputaria.com/video/")
    assert meta.person == "Mini Gabys"
    assert meta.categories[:2] == ["Boquetes", "Bucetas"]
    assert "bunda grande" in meta.tags
    block = format_editorial_block(meta)
    assert "<b>🚫 #Mini_Gabys</b>" in block
    assert "#Pornô_Longo" in block
    assert "#Bunda_Grande" in block
    assert "#Sexo_Ao_Ar_Livre" in block


def test_person_line_is_omitted_when_missing():
    html = """
    <html><body>
      <div class="categories"><a href="/category/amador/">Amador</a></div>
      <div class="tags"><a href="/tag/caseiro/">caseiro</a></div>
    </body></html>
    """
    meta = extract_editorial_metadata(html, "https://xvideosputaria.com/video/")
    block = format_editorial_block(meta)
    assert "🚫" not in block
    assert "#Amador / #caseiro" in block


def test_video_caption_keeps_editorial_block():
    caption = format_video_caption(
        "Título do vídeo",
        {
            "person": "Mini Gabys",
            "categories": ["Boquetes", "Pornô Longo"],
            "tags": ["bunda grande"],
        },
    )
    assert caption.startswith("🎬 Título do vídeo")
    assert "<b>🚫 #Mini_Gabys</b>" in caption
    assert "<blockquote expandable>" in caption
