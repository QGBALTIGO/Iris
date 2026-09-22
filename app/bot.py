from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from app.analyzer import Analyzer
from app.content import chapter_pages, content_images, content_summary
from app.delivery import DeliveryManager, build_pdf, build_zip, human_bytes
from app.jobs import JobStore
from app.models import AnalyzeResult, DownloadJob, JobState, MediaResource, ResourceType
from app.settings import settings
from app.selftest import run_telegram_selftest

_ANALYSES: dict[str, AnalyzeResult] = {}
analyzer = Analyzer()
jobs = JobStore()
delivery = DeliveryManager()

_BUCKET_TYPES = {
    "v": {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM},
    "a": {ResourceType.AUDIO},
    "f": {ResourceType.DOCUMENT, ResourceType.ARCHIVE, ResourceType.SUBTITLE},
}
_BUCKET_NAMES = {
    "v": "Vídeos",
    "a": "Áudios",
    "p": "Páginas do capítulo",
    "i": "Imagens",
    "f": "Arquivos",
}


def _authorized(user_id: int | None) -> bool:
    return settings.admin_id is None or user_id == settings.admin_id


def resources_for_bucket(result: AnalyzeResult, bucket: str) -> list[MediaResource]:
    if bucket == "p":
        return chapter_pages(result)
    if bucket == "i":
        return content_images(result)
    kinds = _BUCKET_TYPES.get(bucket, set())
    return [resource for resource in result.resources if resource.type in kinds]


def needs_deep_analysis(result: AnalyzeResult) -> bool:
    dynamic_types = {ResourceType.VIDEO, ResourceType.AUDIO, ResourceType.PLAYLIST, ResourceType.STREAM}
    if any(resource.type in dynamic_types for resource in result.resources):
        return False
    if chapter_pages(result):
        return False
    return "text/html" in (result.content_type or "").lower() or result.content_type is None


def progress_text(job: DownloadJob, speed_bps: float | None = None) -> str:
    total = len(job.items)
    completed = sum(item.state == JobState.COMPLETED for item in job.items)
    failed = sum(item.state == JobState.FAILED for item in job.items)
    cancelled = sum(item.state == JobState.CANCELLED for item in job.items)
    aggregate = sum(item.progress for item in job.items) / total if total else 0.0
    filled = min(14, round(aggregate * 14))
    bar = "█" * filled + "░" * (14 - filled)
    downloaded = sum(item.bytes_downloaded for item in job.items)
    lines = [
        "IRIS • Download",
        f"{bar}  {aggregate * 100:.0f}%",
        f"Concluídos: {completed}/{total}",
    ]
    if downloaded:
        speed = f" · {human_bytes(int(speed_bps))}/s" if speed_bps and speed_bps > 0 else ""
        lines.append(f"Transferido: {human_bytes(downloaded)}{speed}")
    if failed:
        lines.append(f"Falhas: {failed}")
    if cancelled:
        lines.append(f"Cancelados: {cancelled}")
    running = next((item for item in job.items if item.state == JobState.RUNNING), None)
    if running:
        name = Path(urlsplit(running.url).path).name or "arquivo"
        lines.append(f"Agora: {name[:50]} · {running.progress * 100:.0f}%")
    return "\n".join(lines)


def deliverable_paths(job: DownloadJob, limit_bytes: int) -> tuple[list[Path], list[Path]]:
    sendable: list[Path] = []
    oversized: list[Path] = []
    for item in job.items:
        if item.state != JobState.COMPLETED or not item.output_path:
            continue
        path = Path(item.output_path)
        if not path.is_file():
            continue
        if path.stat().st_size <= limit_bytes:
            sendable.append(path)
        else:
            oversized.append(path)
    return sendable, oversized


def _all_completed_paths(job: DownloadJob) -> list[Path]:
    out: list[Path] = []
    for item in job.items:
        if item.state == JobState.COMPLETED and item.output_path:
            path = Path(item.output_path)
            if path.is_file():
                out.append(path)
    return out


def _domain(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.")


def _short_title(result: AnalyzeResult) -> str:
    value = (result.title or _domain(result.final_url) or result.final_url).strip()
    return value[:120]


def _analysis_text(result: AnalyzeResult, elapsed: float | None = None) -> str:
    stats = content_summary(result)
    lines = ["IRIS • Análise concluída", _short_title(result), _domain(result.final_url), ""]
    if stats["videos"]:
        lines.append(f"Vídeos/streams: {stats['videos']}")
    if stats["audio"]:
        lines.append(f"Áudios: {stats['audio']}")
    if stats["chapter_pages"]:
        lines.append(f"Páginas do capítulo: {stats['chapter_pages']}")
    if stats["images"]:
        lines.append(f"Imagens úteis: {stats['images']}")
    if stats["files"]:
        lines.append(f"Arquivos: {stats['files']}")
    if stats["drm"]:
        lines.append(f"Protegidos por DRM: {stats['drm']}")
    if not any(stats.values()):
        lines.append("Nenhum recurso útil foi identificado.")
    if elapsed is not None:
        lines.extend(["", f"Análise: {elapsed:.1f}s"])
    if result.warnings:
        lines.append(f"Avisos técnicos: {len(result.warnings)}")
    return "\n".join(lines)


async def run_bot() -> None:
    if not settings.bot_token:
        raise RuntimeError("IRIS_BOT_TOKEN não configurado")
    try:
        from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
    except ImportError as exc:
        raise RuntimeError("python-telegram-bot não está instalado") from exc

    def summary_markup(result: AnalyzeResult, key: str):
        rows = []
        for bucket in ("v", "p", "a", "i", "f"):
            count = len(resources_for_bucket(result, bucket))
            if count:
                rows.append([InlineKeyboardButton(f"{_BUCKET_NAMES[bucket]}  {count}", callback_data=f"cat:{bucket}:{key}:0")])
        rows.append([InlineKeyboardButton("Reanalisar profundamente", callback_data=f"deep:{key}")])
        return InlineKeyboardMarkup(rows)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        await update.effective_message.reply_text(
            "IRIS\n\n"
            "Envie um link para localizar vídeos, streams, imagens, capítulos e arquivos. "
            "Também aceito vídeos/documentos enviados diretamente para mostrar os metadados.\n\n"
            "Use /ajuda para ver todas as opções."
        )

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        await update.effective_message.reply_text(
            "IRIS • Ajuda\n\n"
            "/start — abrir o Iris\n"
            "/ajuda — ver recursos e instruções\n"
            "/status — ver motores e configuração\n"
            "/jobs — ver downloads recentes\n"
            "/userbot — ver entrega de arquivos grandes\n"
            "/diagnostico — testar texto, imagem, PDF e vídeo\n"
            "/limpar — apagar downloads temporários\n\n"
            "Envie uma URL diretamente. Em leitores de mangá, o Iris separa páginas do capítulo de logos e banners. "
            "Vídeos podem ser entregues como vídeo ou como arquivo; capítulos podem virar PDF ou ZIP."
        )

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        running = sum(j.state in {JobState.QUEUED, JobState.RUNNING} for j in jobs.list())
        await update.effective_message.reply_text(
            "IRIS • Status\n\n"
            f"Playwright: {'ativo' if settings.browser_enabled else 'desativado'}\n"
            f"yt-dlp: {'ativo' if settings.ytdlp_enabled and shutil.which('yt-dlp') else 'indisponível'}\n"
            f"aria2: {'ativo' if shutil.which('aria2c') else 'indisponível'}\n"
            f"FFmpeg: {'ativo' if shutil.which('ffmpeg') else 'indisponível'}\n"
            f"Userbot: {'configurado' if delivery.userbot_configured else 'não configurado'}\n"
            f"Paralelismo de download: {settings.download_concurrency}\n"
            f"Jobs ativos: {running}"
        )

    async def jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        recent = jobs.list()[:8]
        if not recent:
            await update.effective_message.reply_text("IRIS • Downloads\n\nNenhum job nesta sessão.")
            return
        lines = ["IRIS • Downloads", ""]
        for job in recent:
            done = sum(i.state == JobState.COMPLETED for i in job.items)
            failed = sum(i.state == JobState.FAILED for i in job.items)
            lines.append(f"{job.id} · {job.state.value} · {done}/{len(job.items)} concluídos · {failed} falhas")
        await update.effective_message.reply_text("\n".join(lines))

    async def userbot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        if delivery.userbot_configured:
            text = "IRIS • Userbot\n\nConfigurado. Arquivos acima do limite do Bot API serão encaminhados pelo userbot."
        else:
            text = (
                "IRIS • Userbot\n\nNão configurado. Para arquivos grandes, defina na Railway:\n"
                "IRIS_TELEGRAM_API_ID\nIRIS_TELEGRAM_API_HASH\nIRIS_TELEGRAM_SESSION"
            )
        await update.effective_message.reply_text(text)

    async def diagnostic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        message = await update.effective_message.reply_text(
            "IRIS • Diagnóstico\n\nTestando texto, imagem, PDF e vídeo no Telegram…"
        )
        try:
            results = await run_telegram_selftest(application.bot, update.effective_chat.id)
            await message.edit_text("IRIS • Diagnóstico concluído\n\n" + "\n".join(results))
        except Exception as exc:
            await message.edit_text(f"IRIS • Diagnóstico falhou\n\n{type(exc).__name__}: {str(exc)[:220]}")

    async def clean_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        removed = 0
        settings.downloads_dir.mkdir(parents=True, exist_ok=True)
        for path in settings.downloads_dir.iterdir():
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError:
                pass
        await update.effective_message.reply_text(f"IRIS • Limpeza\n\n{removed} arquivo(s) temporário(s) removido(s).")

    async def on_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        value = (update.effective_message.text or "").strip()
        if not value.startswith(("http://", "https://")):
            await update.effective_message.reply_text("Envie uma URL http:// ou https://, ou use /ajuda.")
            return

        started = time.monotonic()
        status = await update.effective_message.reply_text("IRIS • Analisando\n\nInspecionando página e recursos diretos…")
        try:
            result = await analyzer.analyze(value, deep=False)
            if needs_deep_analysis(result):
                await status.edit_text("IRIS • Analisando\n\nConteúdo dinâmico detectado. Observando a rede da página…")
                result = await analyzer.analyze(value, deep=True)
        except Exception as exc:
            await status.edit_text(
                "IRIS • Falha na análise\n\n"
                f"{type(exc).__name__}: {str(exc)[:180] or 'erro sem detalhes'}"
            )
            return

        key = uuid.uuid4().hex[:10]
        _ANALYSES[key] = result
        elapsed = time.monotonic() - started
        await status.edit_text(_analysis_text(result, elapsed), reply_markup=summary_markup(result, key))

    async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        message = update.effective_message
        obj = message.video or message.audio or message.document or message.animation
        kind = "arquivo"
        if message.video:
            kind = "vídeo"
        elif message.audio:
            kind = "áudio"
        elif message.animation:
            kind = "animação"
        if message.photo:
            obj = message.photo[-1]
            kind = "imagem"
        if obj is None:
            return
        name = getattr(obj, "file_name", None) or f"{kind}-{getattr(obj, 'file_unique_id', 'telegram')}"
        size = getattr(obj, "file_size", None)
        mime = getattr(obj, "mime_type", None) or "—"
        duration = getattr(obj, "duration", None)
        width = getattr(obj, "width", None)
        height = getattr(obj, "height", None)
        lines = [
            "IRIS • Mídia recebida",
            "",
            f"Tipo: {kind}",
            f"Nome: {name}",
            f"Tamanho: {human_bytes(size)}",
            f"MIME: {mime}",
        ]
        if width and height:
            lines.append(f"Resolução: {width}×{height}")
        if duration:
            lines.append(f"Duração: {duration}s")
        lines.extend(["", "O arquivo foi reconhecido pelo Telegram. Para localizar mídia dentro de uma página, envie a URL da página."])
        await message.reply_text("\n".join(lines))

    async def show_category(query, result: AnalyzeResult, key: str, bucket: str, page: int):
        resources = resources_for_bucket(result, bucket)
        per_page = 6
        page = max(0, min(page, max(0, (len(resources) - 1) // per_page)))
        start = page * per_page
        subset = resources[start : start + per_page]
        lines = [f"IRIS • {_BUCKET_NAMES.get(bucket, 'Recursos')}", f"{len(resources)} item(ns)", ""]
        rows = []

        if bucket == "p" and resources:
            rows.append([
                InlineKeyboardButton("Gerar PDF", callback_data=f"bundle:pdf:{bucket}:{key}"),
                InlineKeyboardButton("Gerar ZIP", callback_data=f"bundle:zip:{bucket}:{key}"),
            ])
            rows.append([InlineKeyboardButton("Enviar páginas", callback_data=f"all:{bucket}:{key}:file")])
        elif bucket == "i" and resources:
            rows.append([InlineKeyboardButton("Baixar imagens em ZIP", callback_data=f"bundle:zip:{bucket}:{key}")])

        for offset, resource in enumerate(subset, start=start):
            label = resource.title or Path(urlsplit(resource.url).path).name or resource.type.value
            quality = resource.quality or (f"{resource.height}p" if resource.height else None)
            suffix = f" · {quality}" if quality else ""
            if resource.drm:
                suffix += " · DRM"
            lines.append(f"{offset + 1}. {label[:62]}{suffix}")
            if bucket == "v" and not resource.drm:
                rows.append([InlineKeyboardButton(f"Baixar {offset + 1}", callback_data=f"mode:{bucket}:{key}:{offset}")])
            elif bucket not in {"p", "i"} and not resource.drm:
                rows.append([InlineKeyboardButton(f"Baixar {offset + 1}", callback_data=f"one:{bucket}:{key}:{offset}:file")])

        if bucket not in {"p", "i"} and len(resources) > 1 and any(not r.drm for r in resources):
            rows.append([InlineKeyboardButton(f"Baixar todos ({sum(not r.drm for r in resources)})", callback_data=f"all:{bucket}:{key}:file")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("Anterior", callback_data=f"cat:{bucket}:{key}:{page - 1}"))
        if start + per_page < len(resources):
            nav.append(InlineKeyboardButton("Próxima", callback_data=f"cat:{bucket}:{key}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("Voltar", callback_data=f"home:x:{key}:0")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

    async def watch_job(message, job_id: str, *, delivery_mode: str = "file", bundle_mode: str | None = None):
        last = None
        last_bytes = 0
        last_at = time.monotonic()
        speed = 0.0
        while True:
            job = jobs.get(job_id)
            if not job:
                return
            now = time.monotonic()
            current_bytes = sum(i.bytes_downloaded for i in job.items)
            if now - last_at >= 1:
                speed = max(0.0, (current_bytes - last_bytes) / (now - last_at))
                last_bytes = current_bytes
                last_at = now
            text = progress_text(job, speed)
            if text != last:
                markup = None
                if job.state in {JobState.QUEUED, JobState.RUNNING}:
                    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Cancelar", callback_data=f"cancel:{job.id}")]])
                try:
                    await message.edit_text(text, reply_markup=markup)
                except Exception:
                    pass
                last = text

            if job.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
                paths = _all_completed_paths(job)
                failed = [item for item in job.items if item.state == JobState.FAILED]
                delivered: list[Path] = []
                if paths and job.state != JobState.CANCELLED:
                    try:
                        if bundle_mode == "pdf":
                            out = settings.downloads_dir / f"iris-{job.id}.pdf"
                            build_pdf(paths, out)
                            await delivery.send_path(message, out, caption=f"IRIS · PDF · {len(paths)} página(s)")
                            delivered = paths + [out]
                        elif bundle_mode == "zip":
                            out = settings.downloads_dir / f"iris-{job.id}.zip"
                            build_zip(paths, out)
                            await delivery.send_path(message, out, caption=f"IRIS · ZIP · {len(paths)} arquivo(s)")
                            delivered = paths + [out]
                        else:
                            for path in paths:
                                await delivery.send_path(
                                    message,
                                    path,
                                    as_video=delivery_mode == "video",
                                    caption=f"IRIS · {path.name}",
                                )
                            delivered = paths
                    except Exception as exc:
                        await message.reply_text(f"IRIS • Falha na entrega\n\n{type(exc).__name__}: {str(exc)[:220]}")

                if failed:
                    examples = "\n".join(f"• {Path(urlsplit(i.url).path).name or 'recurso'}: {i.error or 'falha'}" for i in failed[:5])
                    await message.reply_text(
                        f"IRIS • Download parcial\n\n{len(paths)} concluído(s) · {len(failed)} falha(s)\n{examples}"
                    )
                elif job.state == JobState.COMPLETED:
                    await message.reply_text(f"IRIS • Concluído\n\n{len(paths)} arquivo(s) processado(s).")
                await delivery.cleanup(delivered)
                return
            await asyncio.sleep(1.2)

    async def launch_download(query, selected: list[MediaResource], *, delivery_mode: str = "file", bundle_mode: str | None = None):
        selected = [resource for resource in selected if not resource.drm]
        if not selected:
            await query.edit_message_text("IRIS • Indisponível\n\nNenhum recurso baixável nessa seleção.")
            return
        job = jobs.create(selected)
        jobs.launch(job.id)
        await query.edit_message_text(progress_text(job))
        asyncio.create_task(watch_job(query.message, job.id, delivery_mode=delivery_mode, bundle_mode=bundle_mode))

    async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not _authorized(query.from_user.id if query.from_user else None):
            return
        parts = (query.data or "").split(":")
        action = parts[0] if parts else ""

        if action == "cancel" and len(parts) >= 2:
            jobs.cancel(parts[1])
            job = jobs.get(parts[1])
            if job:
                await query.edit_message_text(progress_text(job))
            return

        if action == "home" and len(parts) >= 3:
            key = parts[2]
            result = _ANALYSES.get(key)
            if result:
                await query.edit_message_text(_analysis_text(result), reply_markup=summary_markup(result, key))
            return

        if action == "deep" and len(parts) >= 2:
            key = parts[1]
            result = _ANALYSES.get(key)
            if not result:
                await query.edit_message_text("IRIS • Análise expirada\n\nEnvie a URL novamente.")
                return
            started = time.monotonic()
            await query.edit_message_text("IRIS • Análise profunda\n\nObservando rede, players e conteúdo carregado dinamicamente…")
            deep = await analyzer.analyze(result.final_url, deep=True)
            new_key = uuid.uuid4().hex[:10]
            _ANALYSES[new_key] = deep
            await query.edit_message_text(
                _analysis_text(deep, time.monotonic() - started),
                reply_markup=summary_markup(deep, new_key),
            )
            return

        if action == "cat" and len(parts) >= 4:
            bucket, key, page_raw = parts[1], parts[2], parts[3]
            result = _ANALYSES.get(key)
            if not result:
                await query.edit_message_text("IRIS • Análise expirada\n\nEnvie a URL novamente.")
                return
            await show_category(query, result, key, bucket, int(page_raw))
            return

        if action == "mode" and len(parts) >= 4:
            bucket, key, index_raw = parts[1], parts[2], parts[3]
            result = _ANALYSES.get(key)
            if not result:
                return
            resources = resources_for_bucket(result, bucket)
            index = int(index_raw)
            if not (0 <= index < len(resources)):
                return
            rows = [[
                InlineKeyboardButton("Enviar como vídeo", callback_data=f"one:{bucket}:{key}:{index}:video"),
                InlineKeyboardButton("Enviar como arquivo", callback_data=f"one:{bucket}:{key}:{index}:file"),
            ]]
            rows.append([InlineKeyboardButton("Voltar", callback_data=f"cat:{bucket}:{key}:0")])
            await query.edit_message_text("IRIS • Formato de entrega\n\nComo você quer receber este vídeo?", reply_markup=InlineKeyboardMarkup(rows))
            return

        if action == "bundle" and len(parts) >= 4:
            bundle_mode, bucket, key = parts[1], parts[2], parts[3]
            result = _ANALYSES.get(key)
            if result:
                await launch_download(query, resources_for_bucket(result, bucket), bundle_mode=bundle_mode)
            return

        if action == "one" and len(parts) >= 5:
            bucket, key, index_raw, mode = parts[1], parts[2], parts[3], parts[4]
            result = _ANALYSES.get(key)
            if result:
                resources = resources_for_bucket(result, bucket)
                index = int(index_raw)
                if 0 <= index < len(resources):
                    await launch_download(query, [resources[index]], delivery_mode=mode)
            return

        if action == "all" and len(parts) >= 4:
            bucket, key, mode = parts[1], parts[2], parts[3]
            result = _ANALYSES.get(key)
            if result:
                await launch_download(query, resources_for_bucket(result, bucket), delivery_mode=mode)
            return

    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ajuda", help_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("jobs", jobs_cmd))
    application.add_handler(CommandHandler("userbot", userbot_cmd))
    application.add_handler(CommandHandler("diagnostico", diagnostic_cmd))
    application.add_handler(CommandHandler("limpar", clean_cmd))
    application.add_handler(CallbackQueryHandler(callbacks))
    media_filter = filters.VIDEO | filters.AUDIO | filters.Document.ALL | filters.PHOTO | filters.ANIMATION
    application.add_handler(MessageHandler(media_filter, on_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_url))

    await application.initialize()
    await application.bot.set_my_commands([
        BotCommand("start", "Abrir o Iris"),
        BotCommand("ajuda", "Ver recursos e instruções"),
        BotCommand("status", "Ver motores e configuração"),
        BotCommand("jobs", "Ver downloads recentes"),
        BotCommand("userbot", "Ver entrega de arquivos grandes"),
        BotCommand("diagnostico", "Testar texto, imagem, PDF e vídeo"),
        BotCommand("limpar", "Limpar arquivos temporários"),
    ])
    try:
        await application.bot.set_my_description(
            "Analisador de páginas e gerenciador de mídia: vídeos, streams, imagens, capítulos, PDF, ZIP e downloads em lote."
        )
        await application.bot.set_my_short_description("Analisa páginas e organiza downloads de mídia.")
    except Exception:
        pass
    await application.start()
    await application.updater.start_polling(drop_pending_updates=False)
    if settings.notify_startup and settings.admin_id:
        try:
            results = await run_telegram_selftest(application.bot, settings.admin_id)
            await application.bot.send_message(
                settings.admin_id,
                "IRIS online. Diagnóstico de entrega:\n" + "\n".join(results),
            )
        except Exception:
            pass
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
