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
    assert "#Novinha / #Ruiva" in block
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
    assert "#Amador / #Caseiro" in block


def test_video_caption_keeps_editorial_block():
    caption = format_video_caption(
        "Título do vídeo",
        {
            "person": "Mini Gabys",
            "categories": ["Boquetes", "Pornô Longo"],
            "tags": ["bunda grande"],
        },
    )
    assert caption.startswith("<b>🚫 #Mini_Gabys</b>")
    assert "🎬 Título do vídeo" not in caption
    assert "<blockquote expandable>" in caption
    assert "\n\n<blockquote expandable>" in caption


def test_tubepussy_shorts_taxonomy_is_extracted_from_short_links():
    html = """
    <html><head><title>Mostrando os peitos na frente do espelho</title></head><body>
      <h1>Mostrando os peitos na frente do espelho</h1>
      <div id="shortsInfoPanel" class="shorts-info-panel">
        <div class="sp-tags">
          <a href="/shorts/shorts-porn/">#Shorts Porn</a>
          <a href="/shorts/big-boobs/">#Big boobs</a>
          <a href="/shorts/amateur-porn/">#Amateur porn</a>
          <a href="/shorts/young/">#Young</a>
        </div>
      </div>
      <section class="recommended">
        <div class="video-data">
          <div class="tags-container">
            <a class="video-tag" href="/shorts/amateur/">#Amateur</a>
          </div>
        </div>
        <a href="/shorts/99999/">Outro vídeo</a>
      </section>
    </body></html>
    """
    meta = extract_editorial_metadata(
        html,
        "https://tubepussy.org/shorts/12549/",
    )
    assert meta.tags == [
        "Shorts Porn",
        "Big boobs",
        "Amateur porn",
        "Young",
    ]
    caption = format_video_caption(meta.title, meta)
    assert caption.startswith(
        "<b>🚫 #Mostrando_Os_Peitos_Na_Frente_Do_Espelho</b>"
    )
    assert "#Shorts_Porn" in caption
    assert "#Big_Boobs" in caption
    assert "#Amateur_Porn" in caption
    assert "#Young" in caption
    assert " / #Amateur / " not in caption


def test_xvideosputaria_current_post_taxonomies_ignore_global_menu():
    html = """
    <html><head><title>Vídeo teste - Xvideos Putaria</title></head><body>
      <nav>
        <a href="/videos/porno-longo-qt/">Pornô Longo</a>
        <a href="/videos/famosas-hm/">Famosas</a>
        <a href="/videos/lesbicas-chd-y/">Lésbicas</a>
        <a href="/videos/anal/">Anal</a>
      </nav>
      <div class="post">
        <div class="post-tags">
          <a href="/modelo/mini-gabys-qe/" rel="tag">Mini Gabys</a>
        </div>
        <div class="post-tags">
          <a href="/videos/boquetes-u/">Boquetes</a>
          <a href="/videos/bucetas-s/">Bucetas</a>
          <a href="/videos/bundas-i/">Bundas</a>
          <a href="/videos/gostosas-d/">Gostosas</a>
          <a href="/videos/porno-longo-qt/">Pornô Longo</a>
        </div>
        <div class="post-tags">
          <a href="/xxx/ana-w/" rel="tag">anã</a>
          <a href="/xxx/mamando-rola-y/" rel="tag">mamando rola</a>
          <a href="/xxx/pack-qk/" rel="tag">pack</a>
        </div>
      </div>
    </body></html>
    """
    meta = extract_editorial_metadata(
        html,
        "https://xvideosputaria.com/video-teste/",
    )
    assert meta.person == "Mini Gabys"
    assert meta.categories == [
        "Boquetes",
        "Bucetas",
        "Bundas",
        "Gostosas",
        "Pornô Longo",
    ]
    assert meta.tags == ["anã", "mamando rola", "pack"]
    assert "Famosas" not in meta.categories
    assert "Lésbicas" not in meta.categories
    caption = format_video_caption(meta.title, meta)
    assert caption.startswith("<b>🚫 #Mini_Gabys</b>")
    assert "#Boquetes" in caption
    assert "#Bucetas" in caption
    assert "#Bundas" in caption
    assert "#Anã" in caption
    assert "#Mamando_Rola" in caption
    assert "#Pack" in caption
