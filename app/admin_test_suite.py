from __future__ import annotations

import asyncio
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
from app.video_tools import probe_video


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


async def _run_check(name: str, fn) -> Check:
    started = time.monotonic()
    try:
        detail = await fn()
        return Check(name, "PASS", time.monotonic() - started, str(detail))
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
        raise AssertionError(
            f"nenhuma mídia detectada • avisos={result.warnings[:3]}"
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


async def _public_download(name: str, resource: MediaResource, min_bytes: int = 1024) -> str:
    path = await DownloadEngine().download(resource)
    try:
        size = path.stat().st_size
        if size < min_bytes:
            raise AssertionError(f"arquivo curto: {size} bytes")
        return f"{size / 1024 / 1024:.2f} MB"
    finally:
        path.unlink(missing_ok=True)


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


async def _synthetic_2000() -> str:
    from app.classifier import classify_resource
    from app.dedup import deduplicate

    exts = ["mp4", "webm", "mp3", "jpg", "png", "pdf", "zip", "srt", "m3u8", "mpd"]
    rows: list[MediaResource] = []
    for i in range(2000):
        ext = exts[i % len(exts)]
        url = f"https://cdn.example.test/{i % 53}/asset-{i}.{ext}?v={i}"
        rows.append(MediaResource(url=url, type=classify_resource(url)))
    unique = deduplicate(rows + rows[:500])
    if len(unique) != 2000:
        raise AssertionError(f"dedup={len(unique)}")
    return "2.000 recursos + 500 duplicatas validados"


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
            icon = "✅" if check.status == "PASS" else "❌"
            await bot.send_message(
                admin_id,
                f"{icon} <b>{name}</b> • {check.seconds:.1f}s\n<code>{check.detail[:850]}</code>",
                parse_mode="HTML",
            )

        await add("Ferramentas", lambda: asyncio.sleep(0, result=(
            f"ffmpeg={_tool('ffmpeg')} ffprobe={_tool('ffprobe')} aria2={_tool('aria2c')} "
            f"yt-dlp={_tool('yt-dlp')} N_m3u8DL-RE={_tool('N_m3u8DL-RE')} "
            f"streamlink={_tool('streamlink')} mkvmerge={_tool('mkvmerge')}"
        )))
        await add("Conta 06", lambda: userbot.account_label())
        await add("Stress sintético", _synthetic_2000)
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
                "AnimeOnlineCC",
                "https://animesonlinecc.to/episodio/black-clover-episodio-170/",
                True,
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
            (
                "MANGA Plus",
                "https://mangaplus.shueisha.co.jp/viewer/1009176",
                False,
                False,
            ),
        ]

        for label, url, do_download, expect_drm in real_sites:
            await add(
                label,
                lambda url=url, do_download=do_download, expect_drm=expect_drm, label=label:
                    _analyze_site(label, url, download=do_download, expect_drm=expect_drm),
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
        return checks
