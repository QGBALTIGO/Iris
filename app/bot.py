from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from pathlib import Path

from app.analyzer import Analyzer
from app.jobs import JobStore
from app.models import AnalyzeResult, DownloadJob, JobState, MediaResource, ResourceType
from app.settings import settings

_ANALYSES: dict[str, AnalyzeResult] = {}
analyzer = Analyzer()
jobs = JobStore()

_BUCKET_TYPES = {
    "v": {ResourceType.VIDEO, ResourceType.PLAYLIST},
    "a": {ResourceType.AUDIO},
    "i": {ResourceType.IMAGE},
    "f": {ResourceType.DOCUMENT, ResourceType.ARCHIVE, ResourceType.SUBTITLE},
}
_BUCKET_NAMES = {"v": "Vídeos", "a": "Áudios", "i": "Imagens", "f": "Arquivos"}


def _authorized(user_id: int | None) -> bool:
    return settings.admin_id is None or user_id == settings.admin_id


def resources_for_bucket(result: AnalyzeResult, bucket: str) -> list[MediaResource]:
    kinds = _BUCKET_TYPES.get(bucket, set())
    return [resource for resource in result.resources if resource.type in kinds]


def needs_deep_analysis(result: AnalyzeResult) -> bool:
    dynamic_types = {
        ResourceType.VIDEO,
        ResourceType.AUDIO,
        ResourceType.PLAYLIST,
        ResourceType.STREAM,
    }
    return not any(resource.type in dynamic_types for resource in result.resources)


def progress_text(job: DownloadJob) -> str:
    total = len(job.items)
    completed = sum(item.state == JobState.COMPLETED for item in job.items)
    failed = sum(item.state == JobState.FAILED for item in job.items)
    cancelled = sum(item.state == JobState.CANCELLED for item in job.items)
    if total:
        aggregate = sum(item.progress for item in job.items) / total
    else:
        aggregate = 0.0
    filled = min(16, round(aggregate * 16))
    bar = "█" * filled + "░" * (16 - filled)
    lines = [
        f"Lote {job.id}",
        f"{bar} {aggregate * 100:.0f}%",
        f"Concluídos: {completed}/{total}",
    ]
    if failed:
        lines.append(f"Falhas: {failed}")
    if cancelled:
        lines.append(f"Cancelados: {cancelled}")
    running = next((item for item in job.items if item.state == JobState.RUNNING), None)
    if running:
        name = Path(running.url.split("?", 1)[0]).name or "arquivo"
        lines.append(f"Atual: {name[:55]} · {running.progress * 100:.0f}%")
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


async def run_bot() -> None:
    if not settings.bot_token:
        raise RuntimeError("IRIS_BOT_TOKEN não configurado")
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
    except ImportError as exc:
        raise RuntimeError("python-telegram-bot não está instalado") from exc

    def summary_markup(result: AnalyzeResult, key: str):
        rows = []
        for bucket in ("v", "a", "i", "f"):
            count = len(resources_for_bucket(result, bucket))
            if count:
                rows.append([InlineKeyboardButton(f"{_BUCKET_NAMES[bucket]} · {count}", callback_data=f"cat:{bucket}:{key}:0")])
        rows.append([InlineKeyboardButton("Análise profunda", callback_data=f"deep:{key}")])
        return InlineKeyboardMarkup(rows)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        await update.effective_message.reply_text(
            "Iris pronto. Envie uma URL HTTP/HTTPS. Eu detecto vídeos, streams, áudios, imagens e arquivos da página."
        )

    async def on_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _authorized(update.effective_user.id if update.effective_user else None):
            return
        text = (update.effective_message.text or "").strip()
        if not text.startswith(("http://", "https://")):
            await update.effective_message.reply_text("Envie uma URL começando com http:// ou https://")
            return
        status = await update.effective_message.reply_text("Analisando página…")
        try:
            result = await analyzer.analyze(text, deep=False)
            if needs_deep_analysis(result):
                await status.edit_text("Análise rápida concluída. Procurando recursos dinâmicos…")
                result = await analyzer.analyze(text, deep=True)
        except Exception as exc:
            await status.edit_text(f"Falha na análise: {type(exc).__name__}")
            return
        key = uuid.uuid4().hex[:10]
        _ANALYSES[key] = result
        counts = defaultdict(int)
        for item in result.resources:
            counts[item.type.value] += 1
        lines = [result.title or result.final_url, "", f"Recursos encontrados: {len(result.resources)}"]
        for kind in ("video", "playlist", "audio", "image", "document", "archive", "subtitle"):
            if counts[kind]:
                lines.append(f"{kind}: {counts[kind]}")
        if result.warnings:
            lines.append(f"Avisos: {len(result.warnings)}")
        await status.edit_text("\n".join(lines), reply_markup=summary_markup(result, key))

    async def show_category(query, result: AnalyzeResult, key: str, bucket: str, page: int):
        resources = resources_for_bucket(result, bucket)
        per_page = 6
        page = max(0, min(page, max(0, (len(resources) - 1) // per_page)))
        start = page * per_page
        subset = resources[start : start + per_page]
        lines = [f"{_BUCKET_NAMES.get(bucket, 'Recursos')} · {len(resources)}", ""]
        rows = []
        for offset, resource in enumerate(subset, start=start):
            label = resource.title or Path(resource.url.split("?", 1)[0]).name or resource.type.value
            quality = resource.quality or (f"{resource.height}p" if resource.height else None)
            drm = " · DRM" if resource.drm else ""
            lines.append(f"{offset + 1}. {label[:65]}{f' · {quality}' if quality else ''}{drm}")
            if not resource.drm:
                rows.append([InlineKeyboardButton(f"Baixar {offset + 1}", callback_data=f"one:{bucket}:{key}:{offset}")])
        if subset and any(not r.drm for r in resources):
            rows.append([InlineKeyboardButton(f"Baixar todos ({sum(not r.drm for r in resources)})", callback_data=f"all:{bucket}:{key}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("Anterior", callback_data=f"cat:{bucket}:{key}:{page - 1}"))
        if start + per_page < len(resources):
            nav.append(InlineKeyboardButton("Próxima", callback_data=f"cat:{bucket}:{key}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("Voltar", callback_data=f"home:x:{key}:0")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

    async def watch_job(message, job_id: str):
        last = None
        while True:
            job = jobs.get(job_id)
            if not job:
                return
            text = progress_text(job)
            if text != last:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                markup = None
                if job.state in {JobState.QUEUED, JobState.RUNNING}:
                    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Cancelar lote", callback_data=f"cancel:{job.id}")]])
                try:
                    await message.edit_text(text, reply_markup=markup)
                except Exception:
                    pass
                last = text
            if job.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
                if job.state == JobState.COMPLETED:
                    sendable, oversized = deliverable_paths(job, settings.bot_upload_limit_bytes)
                    for path in sendable:
                        try:
                            with path.open("rb") as fh:
                                await message.reply_document(
                                    document=fh,
                                    filename=path.name,
                                    caption=f"Download concluído · {path.name}",
                                )
                        except Exception as exc:
                            await message.reply_text(f"Concluído, mas não consegui enviar {path.name}: {type(exc).__name__}")
                    if oversized:
                        names = "\n".join(f"• {path.name}" for path in oversized[:10])
                        extra = len(oversized) - 10
                        suffix = f"\n… e mais {extra}" if extra > 0 else ""
                        await message.reply_text(
                            "Downloads concluídos, mas estes arquivos excedem o limite configurado de envio pelo Telegram e ficaram no servidor:\n"
                            f"{names}{suffix}"
                        )
                return
            await asyncio.sleep(1.5)

    async def launch_download(query, selected: list[MediaResource]):
        selected = [resource for resource in selected if not resource.drm]
        if not selected:
            await query.edit_message_text("Nenhum recurso baixável nessa seleção.")
            return
        job = jobs.create(selected)
        jobs.launch(job.id)
        await query.edit_message_text(progress_text(job))
        asyncio.create_task(watch_job(query.message, job.id))

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
                await query.edit_message_text(result.title or result.final_url, reply_markup=summary_markup(result, key))
            return

        if action == "deep" and len(parts) >= 2:
            key = parts[1]
            result = _ANALYSES.get(key)
            if not result:
                await query.edit_message_text("Essa análise expirou. Envie a URL novamente.")
                return
            await query.edit_message_text("Executando análise profunda…")
            deep = await analyzer.analyze(result.final_url, deep=True)
            new_key = uuid.uuid4().hex[:10]
            _ANALYSES[new_key] = deep
            await query.edit_message_text(
                f"{deep.title or deep.final_url}\n\nAnálise profunda: {len(deep.resources)} recursos encontrados.",
                reply_markup=summary_markup(deep, new_key),
            )
            return

        if action == "cat" and len(parts) >= 4:
            bucket, key, page_raw = parts[1], parts[2], parts[3]
            result = _ANALYSES.get(key)
            if not result:
                await query.edit_message_text("Essa análise expirou. Envie a URL novamente.")
                return
            await show_category(query, result, key, bucket, int(page_raw))
            return

        if action in {"one", "all"}:
            if action == "one" and len(parts) >= 4:
                bucket, key, index_raw = parts[1], parts[2], parts[3]
                result = _ANALYSES.get(key)
                if result:
                    resources = resources_for_bucket(result, bucket)
                    index = int(index_raw)
                    if 0 <= index < len(resources):
                        await launch_download(query, [resources[index]])
                return
            if action == "all" and len(parts) >= 3:
                bucket, key = parts[1], parts[2]
                result = _ANALYSES.get(key)
                if result:
                    await launch_download(query, resources_for_bucket(result, bucket))
                return

    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_url))
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
