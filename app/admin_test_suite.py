from __future__ import annotations

import asyncio
import html
import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.analyzer import Analyzer
from app.downloads import DownloadEngine
from app.manifest_tracks import dash_track_details, hls_track_details
from app.models import MediaResource, ResourceType
from app.protection import detect_dash_protection, detect_hls_protection
from app.service_registry import detect_service
from app.settings import settings
from app.userbot import userbot
from app.video_candidates import download_first_valid_video
from app.video_tools import normalize_video_mp4, probe_video


_LOCK = asyncio.Lock()


@dataclass(slots=True)
class Check:
    name: str
    status: str
    seconds: float
    detail: str


def _tool(name: str) -> str:
    path = shutil.which(name)
    return "ok" if path else "ausente"


async def _send_chunks(bot, chat_id: int, text: str) -> None:
    while text:
        chunk = text[:3500]
        if len(text) > 3500:
            cut = chunk.rfind("\n")
            if cut > 1800:
                chunk = chunk[:cut]
        await bot.send_message(chat_id, chunk, parse_mode="HTML")
        text = text[len(chunk):].lstrip("\n")


async def _run_check(name: str, fn, timeout: float = 900.0) -> Check:
    started = time.monotonic()
    try:
        detail = await asyncio.wait_for(fn(), timeout=timeout)
        return Check(name, "PASS", time.monotonic() - started, str(detail))
    except asyncio.TimeoutError:
        return Check(name, "FAIL", time.monotonic() - started, f"Timeout após {timeout:.0f}s")
    except Exception as exc:
        return Check(
            name,
            "FAIL",
            time.monotonic() - started,
            f"{type(exc).__name__}: {str(exc)[:700]}",
        )


async def _analyze_site(name: str, url: str, *, download: bool = False, expect_drm: bool = False) -> str:
    result = await Analyzer().analyze(url, deep=True)
    media = [
        r for r in result.resources
        if r.type in {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM}
    ]
    profile = detect_service(url)
    drm = [r for r in media if r.drm]
    manifests = [r for r in media if r.type == ResourceType.PLAYLIST]
    audios = sum(len(r.metadata.get("audio_tracks") or []) for r in manifests)
    subs = sum(len(r.metadata.get("subtitle_tracks") or []) for r in manifests)
    variants = sum(len(r.variants) for r in manifests)

    if expect_drm:
        if not drm:
            raise AssertionError("DRM esperado, mas não foi detectado")
        systems = sorted({
            s for r in drm for s in (r.metadata.get("drm_systems") or [])
        })
        return (
            f"{profile.label}: DRM detectado"
            + (f" ({', '.join(systems)})" if systems else "")
            + f" • mídia={len(media)} • {result.warnings[:2]}"
        )

    if not media:
        anti_bot = next(
            (
                warning for warning in result.warnings
                if "Cloudflare" in warning or "anti-bot" in warning
            ),
            None,
        )
        if anti_bot:
            raise AssertionError(f"{profile.label}: {anti_bot}")
        raise AssertionError(
            f"{profile.label}: nenhuma mídia detectada • avisos={result.warnings[:3]}"
        )

    if not download:
        return (
            f"{profile.label}: mídia={len(media)} manifests={len(manifests)} "
            f"variantes={variants} áudios={audios} legendas={subs}"
        )

    resource, path, generated, info, rejected = await download_first_valid_video(
        result.resources
    )
    try:
        if info.duration < 5:
            raise AssertionError(f"duração curta: {info.duration:.1f}s")
        if info.width < 160 or info.height < 90:
            raise AssertionError(f"resolução inválida: {info.width}x{info.height}")
        return (
            f"{profile.label}: {path.stat().st_size / 1024 / 1024:.1f} MB • "
            f"{info.duration:.1f}s • {info.width}x{info.height} • "
            f"{info.codec}/{info.audio_codec or '-'} • "
            f"origem={resource.source} • rejeitados={len(rejected)}"
        )
    finally:
        path.unlink(missing_ok=True)


async def _analyze_reader(url: str) -> str:
    result = await Analyzer().analyze(url, deep=True)
    pages = [r for r in result.resources if r.metadata.get("role") == "chapter_page"]
    if not pages:
        raise AssertionError(f"nenhuma página detectada • avisos={result.warnings[:3]}")
    protected = sum(r.metadata.get("raw_downloadable") is False for r in pages)
    return f"páginas={len(pages)} • exportação protegida={protected}"


async def _public_download(name: str, resource: MediaResource, min_bytes: int = 1024) -> str:
    path = await DownloadEngine().download(resource)
    try:
        size = path.stat().st_size
        if size < min_bytes:
            raise AssertionError(f"arquivo curto: {size} bytes")
        return f"{size / 1024 / 1024:.2f} MB"
    finally:
        path.unlink(missing_ok=True)


async def _userbot_check() -> str:
    if not await userbot.is_authorized():
        raise AssertionError("Conta 06 não autenticada")
    return await userbot.account_label()


async def _synthetic_manifest_check() -> str:
    hls = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Português",LANGUAGE="pt-BR",DEFAULT=YES,URI="audio/pt.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",URI="audio/en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="Português",LANGUAGE="pt-BR",URI="subs/pt.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,AUDIO="aud",SUBTITLES="sub"
1080.m3u8
"""
    details = hls_track_details(hls, "https://cdn.example/master.m3u8")
    if len(details["audio_tracks"]) != 2 or len(details["subtitle_tracks"]) != 1:
        raise AssertionError(details)

    mpd = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>
    <AdaptationSet contentType="audio" lang="pt-BR" codecs="mp4a.40.2"><Representation id="a1"/></AdaptationSet>
    <AdaptationSet contentType="text" lang="en"><Representation id="s1" mimeType="text/vtt"/></AdaptationSet>
    </Period></MPD>"""
    ddetails = dash_track_details(mpd, "https://cdn.example/manifest.mpd")
    if len(ddetails["audio_tracks"]) != 1 or len(ddetails["subtitle_tracks"]) != 1:
        raise AssertionError(ddetails)
    return "HLS 2 áudios + 1 legenda; DASH 1 áudio + 1 legenda"


async def _synthetic_drm_check() -> str:
    hls = '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://x"\n'
    hp = detect_hls_protection(hls)
    mpd = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><AdaptationSet>
    <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
    </AdaptationSet></Period></MPD>"""
    dp = detect_dash_protection(mpd)
    if not hp.drm or "FairPlay" not in hp.systems:
        raise AssertionError(hp)
    if not dp.drm or "Widevine" not in dp.systems:
        raise AssertionError(dp)
    return "FairPlay + Widevine reconhecidos"


async def _synthetic_10000() -> str:
    from app.classifier import classify_resource
    from app.dedup import deduplicate

    exts = ["mp4", "webm", "mp3", "jpg", "png", "pdf", "zip", "srt", "m3u8", "mpd"]
    rows: list[MediaResource] = []
    for i in range(10_000):
        ext = exts[i % len(exts)]
        url = f"https://cdn.example.test/{i % 211}/asset-{i}.{ext}?v={i}"
        rows.append(MediaResource(url=url, type=classify_resource(url)))
    unique = deduplicate(rows + rows[:2_500])
    if len(unique) != 10_000:
        raise AssertionError(f"dedup={len(unique)}")
    return "10.000 recursos + 2.500 duplicatas validados"



async def _tool_versions() -> str:
    commands = {
        "ffmpeg": ["ffmpeg", "-version"],
        "ffprobe": ["ffprobe", "-version"],
        "aria2": ["aria2c", "--version"],
        "yt-dlp": ["yt-dlp", "--version"],
        "N_m3u8DL-RE": ["N_m3u8DL-RE", "--version"],
        "streamlink": ["streamlink", "--version"],
        "mkvmerge": ["mkvmerge", "--version"],
    }
    rows = []
    for label, cmd in commands.items():
        if not shutil.which(cmd[0]):
            rows.append(f"{label}=ausente")
            continue
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        first = (out.decode(errors="replace").strip().splitlines() or ["?"])[0]
        rows.append(f"{label}={first[:100]}")
    return " | ".join(rows)


async def _service_registry_check() -> str:
    samples = {
        "crunchyroll": "https://www.crunchyroll.com/watch/x",
        "netflix": "https://www.netflix.com/watch/1",
        "primevideo": "https://www.primevideo.com/detail/x",
        "disneyplus": "https://www.disneyplus.com/video/x",
        "max": "https://play.max.com/video/x",
        "paramountplus": "https://www.paramountplus.com/shows/x",
        "appletv": "https://tv.apple.com/br/movie/x",
        "globoplay": "https://globoplay.globo.com/v/x",
        "hidive": "https://www.hidive.com/video/x",
        "adn": "https://animationdigitalnetwork.fr/video/x",
        "animefire": "https://animefire.io/anime/eU7t5IvcNKU",
        "tubepussy": "https://tubepussy.org/x/",
        "xvideosputaria": "https://xvideosputaria.com/x/",
        "mangaplus": "https://mangaplus.shueisha.co.jp/viewer/1",
        "generic": "https://example.com/video",
    }
    mismatches = []
    for expected, url in samples.items():
        got = detect_service(url).key
        if got != expected:
            mismatches.append(f"{expected}->{got}")
    if mismatches:
        raise AssertionError(", ".join(mismatches))
    return f"{len(samples)} perfis de serviço reconhecidos"


async def _engine_matrix_check() -> str:
    engine = DownloadEngine()
    samples = [
        (MediaResource(url="https://cdn.example/video.mp4", type=ResourceType.VIDEO), {"aria2", "httpx"}),
        (MediaResource(url="https://cdn.example/master.m3u8", type=ResourceType.PLAYLIST), {"n_m3u8dl-re", "yt-dlp", "unsupported-stream"}),
        (MediaResource(url="https://cdn.example/cover.jpg", type=ResourceType.IMAGE), {"httpx"}),
    ]
    rows = []
    for resource, expected in samples:
        selected = engine.choose_engine(resource)
        if selected not in expected:
            raise AssertionError(f"{resource.type}: {selected}")
        rows.append(f"{resource.type.value}={selected}")
    return " • ".join(rows)


async def _candidate_filter_check() -> str:
    from app.video_candidates import video_candidate_rank
    from urllib.parse import urlsplit

    rows = [
        MediaResource(
            url="https://cdn.example/hls/video0.ts",
            type=ResourceType.VIDEO,
            source="browser:network",
            metadata={"hls_segment": True},
        ),
        MediaResource(
            url="https://cdn.example/master.m3u8",
            type=ResourceType.PLAYLIST,
            source="browser:network",
        ),
        MediaResource(
            url="https://cdn.example/video.mp4",
            type=ResourceType.VIDEO,
            source="browser:network",
        ),
    ]
    usable = [
        r for r in rows
        if not r.metadata.get("hls_segment")
        and not (r.source.startswith("browser:") and urlsplit(r.url).path.lower().endswith(".ts"))
    ]
    usable.sort(key=video_candidate_rank)
    if [urlsplit(r.url).path for r in usable] != ["/video.mp4", "/master.m3u8"]:
        raise AssertionError([r.url for r in usable])
    return "segmento .ts descartado; MP4 > playlist"


async def _video_normalization_check() -> str:
    root = Path("/data/iris-test-temp") if Path("/data").exists() else settings.downloads_dir / "test-temp"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "odd-source.mkv"
    output = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=405x719:rate=17",
            "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=32000",
            "-t", "1.2",
            "-c:v", "ffv1",
            "-pix_fmt", "yuv444p",
            "-c:a", "pcm_s16le",
            "-y", str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-500:])
        output, generated = await normalize_video_mp4(source)
        info = await probe_video(output)
        if output.suffix.lower() != ".mp4":
            raise AssertionError(output)
        if info.width % 2 or info.height % 2:
            raise AssertionError(f"{info.width}x{info.height}")
        if info.audio_codec != "aac":
            raise AssertionError(info.audio_codec)
        if info.codec not in {"h264", "mpeg4"}:
            raise AssertionError(info.codec)
        return f"{info.codec}/aac • {info.width}x{info.height} • {output.stat().st_size/1024:.0f} KB"
    finally:
        source.unlink(missing_ok=True)
        if output and output != source:
            output.unlink(missing_ok=True)


async def _download_speed_check(size_mb: int, url: str) -> str:
    started = time.monotonic()
    path = await DownloadEngine().download(
        MediaResource(url=url, type=ResourceType.OTHER),
        filename=f"speed-{size_mb}mb.bin",
    )
    try:
        elapsed = max(time.monotonic() - started, 0.001)
        size = path.stat().st_size
        mib_s = size / 1024 / 1024 / elapsed
        if size < size_mb * 700_000:
            raise AssertionError(f"arquivo curto: {size}")
        return f"{size/1024/1024:.1f} MB em {elapsed:.2f}s • {mib_s:.1f} MB/s"
    finally:
        path.unlink(missing_ok=True)

async def run_admin_test_suite(bot, admin_id: int) -> list[Check]:
    if _LOCK.locked():
        await bot.send_message(admin_id, "🧪 <b>Já existe uma bateria de testes em execução.</b>", parse_mode="HTML")
        return []

    async with _LOCK:
        await bot.send_message(
            admin_id,
            "🧪 <b>IRIS • Bateria completa</b>\n\n"
            "Vou testar motores, manifestos, navegador, downloads reais, DRM detection e sites do seu fluxo. "
            "O relatório final será salvo em <code>/data</code>.",
            parse_mode="HTML",
        )

        checks: list[Check] = []

        async def add(name: str, fn):
            check = await _run_check(name, fn)
            checks.append(check)
            print(
                "IRIS_TEST_RESULT "
                + json.dumps(
                    {
                        "name": check.name,
                        "status": check.status,
                        "seconds": round(check.seconds, 3),
                        "detail": check.detail,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            icon = "✅" if check.status == "PASS" else "❌"
            await bot.send_message(
                admin_id,
                f"{icon} <b>{html.escape(name)}</b> • {check.seconds:.1f}s\n"
                f"<code>{html.escape(check.detail[:850])}</code>",
                parse_mode="HTML",
            )

        await add("Ferramentas", lambda: asyncio.sleep(0, result=(
            f"ffmpeg={_tool('ffmpeg')} ffprobe={_tool('ffprobe')} aria2={_tool('aria2c')} "
            f"yt-dlp={_tool('yt-dlp')} N_m3u8DL-RE={_tool('N_m3u8DL-RE')} "
            f"streamlink={_tool('streamlink')} mkvmerge={_tool('mkvmerge')}"
        )))
        await add("Versões dos motores", _tool_versions)
        await add("Registro de serviços", _service_registry_check)
        await add("Matriz de motores", _engine_matrix_check)
        await add("Filtro de candidatos", _candidate_filter_check)
        await add("Normalização MP4 difícil", _video_normalization_check)
        await add("Conta 06", _userbot_check)
        await add("Stress sintético 10k", _synthetic_10000)
        await add("Faixas HLS/DASH", _synthetic_manifest_check)
        await add("Classificação DRM", _synthetic_drm_check)

        await add(
            "JPEG público",
            lambda: _public_download(
                "jpeg",
                MediaResource(url="https://httpbin.org/image/jpeg", type=ResourceType.IMAGE),
                10_000,
            ),
        )
        await add(
            "Download 8 MB",
            lambda: _download_speed_check(
                8,
                "https://media.githubusercontent.com/media/inventer-dev/speed-test-files/main/8MB.bin",
            ),
        )
        await add(
            "Download 32 MB",
            lambda: _download_speed_check(
                32,
                "https://media.githubusercontent.com/media/inventer-dev/speed-test-files/main/32MB.bin",
            ),
        )
        await add(
            "PDF público",
            lambda: _public_download(
                "pdf",
                MediaResource(
                    url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
                    type=ResourceType.DOCUMENT,
                ),
                5_000,
            ),
        )
        await add(
            "MP4 direto",
            lambda: _analyze_site(
                "sample-mp4",
                "https://download.samplelib.com/mp4/sample-5s.mp4",
                download=True,
            ),
        )
        await add(
            "HLS público",
            lambda: _analyze_site(
                "hls",
                "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
                download=True,
            ),
        )
        await add(
            "DASH público",
            lambda: _analyze_site(
                "dash",
                "https://storage.googleapis.com/shaka-demo-assets/angel-one/dash.mpd",
                download=False,
            ),
        )

        real_sites = [
            (
                "AnimeFire",
                "https://animefire.io/anime/eU7t5IvcNKU",
                False,
                False,
            ),
            (
                "Pobreflix",
                "https://www.pobrenow.com/filme/vamos-time-2026",
                True,
                False,
            ),
            (
                "TubePussy",
                "https://tubepussy.org/ruiva-isabel-dando-a-bucetinha-e-levando-na-cara/#forward",
                True,
                False,
            ),
            (
                "XVideosPutaria",
                "https://xvideosputaria.com/anao-gabriela-gadotti-mini-gabys-boquetando-com-leite-na-boca/#forward",
                True,
                False,
            ),
            (
                "Crunchyroll",
                "https://www.crunchyroll.com/pt-br/watch/GMKUX478J/jobless-reincarnation",
                False,
                True,
            ),
        ]

        for label, url, do_download, expect_drm in real_sites:
            await add(
                label,
                lambda url=url, do_download=do_download, expect_drm=expect_drm, label=label:
                    _analyze_site(label, url, download=do_download, expect_drm=expect_drm),
            )

        await add(
            "MANGA Plus",
            lambda: _analyze_reader("https://mangaplus.shueisha.co.jp/viewer/1009176"),
        )

        passed = sum(c.status == "PASS" for c in checks)
        failed = len(checks) - passed
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "failed": failed,
            "checks": [asdict(c) for c in checks],
        }
        report_dir = Path("/data/iris-test-reports") if Path("/data").exists() else settings.downloads_dir / "test-reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"iris-tests-{int(time.time())}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        lines = [
            "📊 <b>IRIS • Resultado final</b>",
            "",
            f"✅ Passaram: <b>{passed}</b>",
            f"❌ Falharam: <b>{failed}</b>",
            f"🧪 Total: <b>{len(checks)}</b>",
            "",
        ]
        for check in checks:
            icon = "✅" if check.status == "PASS" else "❌"
            lines.append(f"{icon} {check.name} • {check.seconds:.1f}s")
        lines.extend(["", f"💾 Relatório: <code>{report_path}</code>"])
        await _send_chunks(bot, admin_id, "\n".join(lines))
        try:
            from telegram import InputFile
            with report_path.open("rb") as fh:
                await bot.send_document(
                    chat_id=admin_id,
                    document=InputFile(fh, filename=report_path.name),
                    caption=(
                        f"📎 IRIS • relatório detalhado\n"
                        f"✅ {passed} • ❌ {failed} • 🧪 {len(checks)}"
                    ),
                )
        except Exception as exc:
            await bot.send_message(
                admin_id,
                f"⚠️ Não consegui anexar o relatório: <code>{html.escape(str(exc)[:250])}</code>",
                parse_mode="HTML",
            )
        return checks
