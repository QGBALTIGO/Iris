from __future__ import annotations

import asyncio
import html
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.analyzer import Analyzer
from app.benchmark import run_admin_benchmark
from app.content import chapter_pages, content_images, content_summary
from app.delivery import DeliveryManager, build_pdf, build_zip, human_bytes, parse_relay_payload
from app.jobs import JobStore
from app.large_video_smoke import run_large_video_smoke
from app.models import AnalyzeResult, DownloadJob, JobState, MediaResource, ResourceType
from app.mtproto_speed_smoke import run_mtproto_speed_smoke
from app.selftest import run_telegram_selftest
from app.settings import settings
from app.site_queue import site_queue
from app.source_speed_smoke import run_source_speed_smoke
from app.userbot import userbot
from app.video_smoke import run_native_video_smoke

_ANALYSES: dict[str, AnalyzeResult] = {}
_AUTH_STAGE: str | None = None

analyzer = Analyzer()
jobs = JobStore()
delivery = DeliveryManager()

_BUCKET_TYPES = {
    "v": {ResourceType.VIDEO, ResourceType.PLAYLIST, ResourceType.STREAM},
    "a": {ResourceType.AUDIO},
    "f": {ResourceType.DOCUMENT, ResourceType.ARCHIVE, ResourceType.SUBTITLE},
}
_BUCKET_NAMES = {
    "v": "🎬 Vídeos",
    "a": "🎵 Áudios",
    "p": "📖 Páginas",
    "i": "🖼️ Imagens",
    "f": "📦 Arquivos",
}


def _is_admin(user_id: int | None) -> bool:
    return bool(settings.admin_id and user_id == settings.admin_id)


def _can_use(user_id: int | None) -> bool:
    return _is_admin(user_id) or settings.public_enabled


def _safe(value: str | None, limit: int = 160) -> str:
    return html.escape((value or "").strip()[:limit])


def _domain(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.")


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
    aggregate = sum(item.progress for item in job.items) / total if total else 0.0
    filled = min(12, round(aggregate * 12))
    bar = "█" * filled + "░" * (12 - filled)
    downloaded = sum(item.bytes_downloaded for item in job.items)
    lines = [
        "⬇️ <b>Baixando</b>",
        f"<code>{bar}</code>  <b>{aggregate * 100:.0f}%</b>",
        "",
        f"📦 Concluídos: {completed}/{total}",
    ]
    if downloaded:
        speed = f" • ⚡ {human_bytes(int(speed_bps))}/s" if speed_bps and speed_bps > 0 else ""
        lines.append(f"💾 {human_bytes(downloaded)}{speed}")
    if failed:
        lines.append(f"⚠️ {failed} falha(s)")
    running = next((item for item in job.items if item.state == JobState.RUNNING), None)
    if running:
        name = unquote(Path(urlsplit(running.url).path).name) or "arquivo"
        lines.extend(["", f"⏳ <i>{_safe(name, 52)}</i>"])
    return "\n".join(lines)


def upload_progress_text(name: str, sent: int, total: int, speed_bps: float = 0.0) -> str:
    value = min(1.0, sent / total) if total else 0.0
    filled = min(12, round(value * 12))
    bar = "█" * filled + "░" * (12 - filled)
    speed = f" • ⚡ {human_bytes(int(speed_bps))}/s" if speed_bps > 0 else ""
    return "\n".join([
        "⬆️ <b>Enviando para o Telegram</b>",
        f"<code>{bar}</code>  <b>{value * 100:.0f}%</b>",
        "",
        f"💾 {human_bytes(sent)} / {human_bytes(total)}{speed}",
        f"🎬 <i>{_safe(name, 52)}</i>",
    ])


def delivery_caption(resource: MediaResource | None, *, as_video: bool = False) -> str:
    title = None
    quality = None
    if resource is not None:
        title = resource.title or resource.metadata.get("page_title")
        quality = resource.quality or (f"{resource.height}p" if resource.height else None)
    title = title or ("Vídeo" if as_video else "Arquivo")
    icon = "🎬" if as_video else "📦"
    lines = [f"{icon} <b>{_safe(str(title), 220)}</b>"]
    if quality:
        lines.append(f"📺 <b>{_safe(str(quality), 30)}</b>")
    lines.extend(["", "✨ <i>IRIS</i>"])
    return "\n".join(lines)


def queue_status_text() -> str:
    status = site_queue.status()
    counts = status.get("counts") or {}
    pending = int(counts.get("pending", 0)) + int(counts.get("retry_local", 0))
    sent = int(counts.get("sent", 0))
    failed = int(counts.get("failed", 0))
    processing = int(counts.get("processing", 0)) + int(counts.get("awaiting_delivery", 0))
    total = int(status.get("discovered_total") or 0)
    state = "⏸️ Pausada" if status.get("paused") else ("▶️ Rodando" if status.get("running") else "⏹️ Parada")
    lines = [
        "🎞️ <b>Fila do site</b>",
        "",
        f"Estado: <b>{state}</b>",
        f"📚 Descobertos: <b>{total}</b>",
        f"✅ Enviados: <b>{sent}</b>",
        f"⏳ Pendentes: <b>{pending}</b>",
        f"⚙️ Em processamento: <b>{processing}</b>",
        f"❌ Falhas: <b>{failed}</b>",
    ]
    current = status.get("current")
    if current:
        title = current.get("title") or current.get("url") or "item"
        lines.extend(["", f"Agora: <i>{_safe(str(title), 110)}</i>"])
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


def _analysis_text(result: AnalyzeResult, elapsed: float | None = None) -> str:
    stats = content_summary(result)
    title = _safe(result.title or _domain(result.final_url) or result.final_url, 110)
    lines = [
        "🔎 <b>Análise concluída</b>",
        "",
        f"🎯 <b>{title}</b>",
        f"🌐 <code>{_safe(_domain(result.final_url), 80)}</code>",
        "",
    ]
    if stats["videos"]:
        lines.append(f"🎬 Vídeos/streams: <b>{stats['videos']}</b>")
    if stats["audio"]:
        lines.append(f"🎵 Áudios: <b>{stats['audio']}</b>")
    if stats["chapter_pages"]:
        protected = any(r.metadata.get("raw_downloadable") is False for r in chapter_pages(result))
        lock = " 🔒" if protected else ""
        lines.append(f"📖 Páginas do capítulo: <b>{stats['chapter_pages']}</b>{lock}")
    if stats["images"]:
        lines.append(f"🖼️ Imagens úteis: <b>{stats['images']}</b>")
    if stats["files"]:
        lines.append(f"📦 Arquivos: <b>{stats['files']}</b>")
    if stats["drm"]:
        lines.append(f"🔒 Mídia protegida: <b>{stats['drm']}</b>")
    if not any(stats.values()):
        lines.append("🤷 Nenhum recurso útil encontrado.")
    if elapsed is not None:
        lines.extend(["", f"⏱️ <i>{elapsed:.1f}s</i>"])
    return "\n".join(lines)


def _all_completed_paths(job: DownloadJob) -> list[Path]:
    paths: list[Path] = []
    for item in job.items:
        if item.state == JobState.COMPLETED and item.output_path:
            path = Path(item.output_path)
            if path.is_file():
                paths.append(path)
    return paths


async def run_bot() -> None:
    global _AUTH_STAGE
    if not settings.bot_token:
        raise RuntimeError("IRIS_BOT_TOKEN não configurado")

    from telegram import (
        BotCommand,
        BotCommandScopeChat,
        BotCommandScopeDefault,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        Update,
    )
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        Defaults,
        MessageHandler,
        filters,
    )

    defaults = Defaults(parse_mode=ParseMode.HTML)
    application = Application.builder().token(settings.bot_token).defaults(defaults).build()

    def summary_markup(result: AnalyzeResult, key: str):
        rows = []
        for bucket in ("v", "p", "a", "i", "f"):
            count = len(resources_for_bucket(result, bucket))
            if count:
                rows.append([
                    InlineKeyboardButton(
                        f"{_BUCKET_NAMES[bucket]} · {count}",
                        callback_data=f"cat:{bucket}:{key}:0",
                    )
                ])
        rows.append([
            InlineKeyboardButton("🔬 Reanalisar profundamente", callback_data=f"deep:{key}")
        ])
        return InlineKeyboardMarkup(rows)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if not _can_use(uid):
            return
        await update.effective_message.reply_text(
            "✨ <b>IRIS</b>\n\n"
            "Envie um <b>link</b> e eu procuro vídeos, streams, imagens, páginas, áudios e arquivos.\n\n"
            "🎬 vídeos e players\n"
            "📖 capítulos e leitores\n"
            "🖼️ imagens\n"
            "📦 arquivos\n"
            "⚡ downloads em lote\n\n"
            "É só mandar a URL."
        )

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if not _can_use(uid):
            return
        await update.effective_message.reply_text(
            "💡 <b>Como usar</b>\n\n"
            "1. Envie uma URL.\n"
            "2. O Iris identifica o conteúdo disponível.\n"
            "3. Escolha o que quer baixar.\n"
            "4. Para vídeos, escolha receber como vídeo ou arquivo.\n"
            "5. Em leitores compatíveis, páginas podem virar PDF ou ZIP."
        )

    async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update.effective_user.id if update.effective_user else None):
            return
        ready = await userbot.is_authorized()
        label = await userbot.account_label() if ready else "desconectada"
        await update.effective_message.reply_text(
            "🛠️ <b>Status administrativo</b>\n\n"
            f"🌐 Playwright: {'✅' if settings.browser_enabled else '❌'}\n"
            f"🎞️ yt-dlp: {'✅' if settings.ytdlp_enabled and shutil.which('yt-dlp') else '❌'}\n"
            f"⚡ aria2: {'✅' if shutil.which('aria2c') else '❌'}\n"
            f"🎛️ FFmpeg: {'✅' if shutil.which('ffmpeg') else '❌'}\n"
            f"👤 Conta 06: {'✅' if ready else '❌'} {_safe(label, 80)}\n"
            f"🔀 Downloads paralelos: <b>{settings.download_concurrency}</b>"
        )

    async def admin_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update.effective_user.id if update.effective_user else None):
            return
        recent = jobs.list()[:8]
        if not recent:
            await update.effective_message.reply_text("📭 <b>Nenhum download nesta sessão.</b>")
            return
        lines = ["📊 <b>Downloads recentes</b>", ""]
        for job in recent:
            done = sum(i.state == JobState.COMPLETED for i in job.items)
            failed = sum(i.state == JobState.FAILED for i in job.items)
            lines.append(f"<code>{job.id}</code> • {done}/{len(job.items)} • ⚠️ {failed}")
        await update.effective_message.reply_text("\n".join(lines))

    async def admin_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
        global _AUTH_STAGE
        if not _is_admin(update.effective_user.id if update.effective_user else None):
            return
        if await userbot.is_authorized():
            await update.effective_message.reply_text(
                "✅ <b>Conta 06 conectada</b>\n\n"
                f"👤 {_safe(await userbot.account_label(), 100)}\n"
                "⚡ Arquivos grandes já podem usar MTProto."
            )
            return
        try:
            result = await userbot.begin_login()
            if result.startswith("already:"):
                _AUTH_STAGE = None
                await update.effective_message.reply_text("✅ <b>Conta 06 já está conectada.</b>")
                return
            _AUTH_STAGE = "code"
            await update.effective_message.reply_text(
                "📲 <b>Conta 06</b>\n\n"
                "Enviei um código de acesso para a conta.\n"
                "Mande <b>somente o código</b> aqui nesta conversa.\n\n"
                "🔐 A mensagem será apagada depois da leitura."
            )
        except Exception as exc:
            await update.effective_message.reply_text(
                f"⚠️ <b>Não consegui iniciar o login.</b>\n\n<code>{_safe(str(exc), 180)}</code>"
            )

    async def admin_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update.effective_user.id if update.effective_user else None):
            return
        msg = await update.effective_message.reply_text(
            "🧪 <b>Diagnóstico em andamento</b>\n\n"
            "Testando texto, imagem, PDF, vídeo e vídeo como arquivo…"
        )
        try:
            results = await run_telegram_selftest(application.bot, update.effective_chat.id)
            pretty = "\n".join(f"• {html.escape(line)}" for line in results)
            await msg.edit_text(f"✅ <b>Diagnóstico concluído</b>\n\n{pretty}")
        except Exception as exc:
            await msg.edit_text(
                f"⚠️ <b>Diagnóstico falhou</b>\n\n<code>{_safe(str(exc), 220)}</code>"
            )

    async def admin_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update.effective_user.id if update.effective_user else None):
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
        await update.effective_message.reply_text(
            f"🧹 <b>Limpeza concluída</b>\n\n{removed} arquivo(s) removido(s)."
        )

    async def admin_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update.effective_user.id if update.effective_user else None):
            return
        command = (update.effective_message.text or "").split()[0].split("@", 1)[0].lower()
        if command == "/retomar" and not context.args:
            action = "continuar"
        else:
            action = (context.args[0].lower() if context.args else "status")
        message = update.effective_message
        chat_id = update.effective_chat.id

        if action in {"status", "estado"}:
            await message.reply_text(queue_status_text())
            return

        if action in {"iniciar", "start"}:
            wait = await message.reply_text(
                "🔎 <b>Montando a fila…</b>\n\nBuscando todas as postagens do site."
            )
            try:
                stats = await site_queue.discover()
                await site_queue.start(application.bot, chat_id, discover=False)
                await wait.edit_text(
                    "▶️ <b>Fila iniciada</b>\n\n"
                    f"📚 Encontrados: <b>{stats['found']}</b>\n"
                    f"➕ Novos: <b>{stats['added']}</b>\n"
                    f"🎞️ Total registrado: <b>{stats['total']}</b>\n\n"
                    "Vou enviar um por um. O progresso fica salvo em /data."
                )
            except Exception as exc:
                await wait.edit_text(
                    "⚠️ <b>Não consegui iniciar a fila.</b>\n\n"
                    f"<code>{_safe(type(exc).__name__ + ': ' + str(exc), 250)}</code>"
                )
            return

        if action in {"continuar", "retomar", "resume"}:
            try:
                await site_queue.resume(application.bot, chat_id)
                await message.reply_text(
                    "▶️ <b>Fila retomada</b>\n\nContinuando exatamente dos itens ainda pendentes."
                )
            except Exception as exc:
                await message.reply_text(
                    f"⚠️ <b>Falha ao retomar.</b>\n\n<code>{_safe(str(exc), 220)}</code>"
                )
            return

        if action in {"pausar", "pause"}:
            site_queue.pause()
            await message.reply_text("⏸️ <b>Fila pausada.</b>\n\nO progresso ficou salvo.")
            return

        if action in {"atualizar", "refresh"}:
            wait = await message.reply_text("🔄 <b>Atualizando catálogo…</b>")
            try:
                stats = await site_queue.discover()
                await wait.edit_text(
                    "✅ <b>Catálogo atualizado</b>\n\n"
                    f"📚 Encontrados: <b>{stats['found']}</b>\n"
                    f"➕ Novos: <b>{stats['added']}</b>\n"
                    f"🎞️ Total: <b>{stats['total']}</b>"
                )
            except Exception as exc:
                await wait.edit_text(f"⚠️ <code>{_safe(str(exc), 250)}</code>")
            return

        if action in {"repetir", "falhas", "retry"}:
            count = site_queue.retry_failures()
            await site_queue.resume(application.bot, chat_id)
            await message.reply_text(
                f"🔁 <b>Falhas recolocadas na fila</b>\n\nItens: <b>{count}</b>"
            )
            return

        await message.reply_text(
            "🎞️ <b>Fila do site</b>\n\n"
            "<code>/fila iniciar</code> — descobrir e começar\n"
            "<code>/fila continuar</code> — retomar de onde parou\n"
            "<code>/fila pausar</code> — pausar\n"
            "<code>/fila status</code> — ver progresso\n"
            "<code>/fila atualizar</code> — buscar novos posts\n"
            "<code>/fila repetir</code> — tentar falhas novamente"
        )

    async def handle_auth_text(update: Update) -> bool:
        global _AUTH_STAGE
        if not _AUTH_STAGE or not _is_admin(update.effective_user.id if update.effective_user else None):
            return False
        value = (update.effective_message.text or "").strip()
        try:
            await update.effective_message.delete()
        except Exception:
            pass
        try:
            if _AUTH_STAGE == "code":
                status = await userbot.submit_code(value)
                if status == "password":
                    _AUTH_STAGE = "password"
                    await update.effective_chat.send_message(
                        "🔐 <b>Verificação em duas etapas</b>\n\n"
                        "Envie a senha da Conta 06. A mensagem também será apagada."
                    )
                else:
                    _AUTH_STAGE = None
                    await update.effective_chat.send_message(
                        "✅ <b>Conta 06 conectada</b>\n\n"
                        "A sessão foi salva no volume persistente da Railway. "
                        "Arquivos grandes já podem ser enviados pelo userbot."
                    )
                return True
            if _AUTH_STAGE == "password":
                await userbot.submit_password(value)
                _AUTH_STAGE = None
                await update.effective_chat.send_message(
                    "✅ <b>Conta 06 conectada</b>\n\n"
                    "Sessão persistente criada com sucesso."
                )
                return True
        except Exception as exc:
            _AUTH_STAGE = None
            await update.effective_chat.send_message(
                f"⚠️ <b>Falha ao conectar a Conta 06</b>\n\n<code>{_safe(str(exc), 180)}</code>"
            )
            return True
        return False

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if await handle_auth_text(update):
            return
        if not _can_use(uid):
            return

        value = (update.effective_message.text or "").strip()
        if not value.startswith(("http://", "https://")):
            await update.effective_message.reply_text(
                "🔗 <b>Envie um link válido</b>\n\n"
                "Cole uma URL começando com <code>http://</code> ou <code>https://</code>."
            )
            return

        started = time.monotonic()
        status = await update.effective_message.reply_text(
            "🔎 <b>Analisando…</b>\n\n"
            "⚡ Procurando recursos diretos."
        )
        try:
            result = await analyzer.analyze(value, deep=False)
            if needs_deep_analysis(result):
                await status.edit_text(
                    "🔎 <b>Analisando…</b>\n\n"
                    "🌐 Página dinâmica detectada. Observando a rede e o player."
                )
                result = await analyzer.analyze(value, deep=True)
        except Exception as exc:
            await status.edit_text(
                "⚠️ <b>Não consegui analisar essa página.</b>\n\n"
                f"<code>{_safe(type(exc).__name__ + ': ' + str(exc), 220)}</code>"
            )
            return

        key = uuid.uuid4().hex[:10]
        _ANALYSES[key] = result
        await status.edit_text(
            _analysis_text(result, time.monotonic() - started),
            reply_markup=summary_markup(result, key),
        )

    async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        message = update.effective_message

        # Internal Account 06 relay. The bot receives the userbot upload with
        # the Bot API's own message_id and can copy it server-side without
        # resolving the admin as an MTProto PeerUser or uploading twice.
        if uid and userbot.configured and await userbot.is_authorized():
            try:
                if uid == await userbot.user_id():
                    relay = parse_relay_payload(message.caption)
                    if relay:
                        target_chat_id = int(relay["chat_id"])
                        clean_caption = str(relay.get("caption") or "")
                        queue_item_id = relay.get("queue_item_id")
                        expected_kind = relay.get("expected_kind")
                        media_kind = (
                            "video" if message.video else
                            "animation" if message.animation else
                            "audio" if message.audio else
                            "voice" if message.voice else
                            "photo" if message.photo else
                            "document" if message.document else
                            "other"
                        )

                        if queue_item_id is not None and expected_kind and media_kind != expected_kind:
                            site_queue.mark_relay_result(
                                int(queue_item_id),
                                media_kind=media_kind,
                                error=f"Esperado {expected_kind}, recebido {media_kind}",
                            )
                            try:
                                await context.bot.delete_message(
                                    chat_id=message.chat_id,
                                    message_id=message.message_id,
                                )
                            except Exception:
                                pass
                            print(
                                f"IRIS_QUEUE_RETRY item={queue_item_id} expected={expected_kind} got={media_kind}",
                                flush=True,
                            )
                            return

                        copied = await context.bot.copy_message(
                            chat_id=target_chat_id,
                            from_chat_id=message.chat_id,
                            message_id=message.message_id,
                            caption=clean_caption or None,
                        )
                        if queue_item_id is not None:
                            site_queue.mark_relay_result(
                                int(queue_item_id),
                                media_kind=media_kind,
                                message_id=getattr(copied, "message_id", None),
                            )
                        media_obj = (
                            message.video
                            or message.animation
                            or message.audio
                            or message.voice
                            or message.document
                        )
                        duration = getattr(media_obj, "duration", None) if media_obj else None
                        width = getattr(media_obj, "width", None) if media_obj else None
                        height = getattr(media_obj, "height", None) if media_obj else None
                        has_thumb = bool(
                            getattr(media_obj, "thumbnail", None)
                            or getattr(media_obj, "cover", None)
                        ) if media_obj else False
                        print(
                            f"IRIS_RELAY_OK source={message.chat_id} message={message.message_id} "
                            f"target={target_chat_id} kind={media_kind} duration={duration} "
                            f"width={width} height={height} thumb={has_thumb}",
                            flush=True,
                        )
                        try:
                            await context.bot.delete_message(
                                chat_id=message.chat_id,
                                message_id=message.message_id,
                            )
                        except Exception:
                            pass
                    return
            except Exception as exc:
                try:
                    payload = parse_relay_payload(message.caption)
                    if payload and payload.get("queue_item_id") is not None:
                        site_queue.mark_relay_result(
                            int(payload["queue_item_id"]),
                            media_kind="error",
                            error=f"{type(exc).__name__}: {str(exc)[:220]}",
                        )
                except Exception:
                    pass
                print(f"IRIS_RELAY_ERROR {type(exc).__name__}: {exc}", flush=True)
                return

        if not _can_use(uid):
            return
        obj = (
            message.video
            or message.video_note
            or message.animation
            or message.audio
            or message.voice
            or message.document
        )
        kind = "arquivo"
        if message.video:
            kind = "vídeo"
        elif message.video_note:
            kind = "vídeo circular"
        elif message.animation:
            kind = "animação"
        elif message.audio:
            kind = "áudio"
        elif message.voice:
            kind = "voz"
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
            "📥 <b>Mídia recebida</b>",
            "",
            f"🎯 Tipo: <b>{_safe(kind)}</b>",
            f"📄 Nome: <code>{_safe(name, 90)}</code>",
            f"💾 Tamanho: <b>{human_bytes(size)}</b>",
            f"🧩 MIME: <code>{_safe(mime, 60)}</code>",
        ]
        if width and height:
            lines.append(f"📐 Resolução: <b>{width}×{height}</b>")
        if duration:
            lines.append(f"⏱️ Duração: <b>{duration}s</b>")
        lines.extend(["", "✅ Recebido e identificado corretamente."])
        await message.reply_text("\n".join(lines))

    async def show_category(query, result: AnalyzeResult, key: str, bucket: str, page: int):
        resources = resources_for_bucket(result, bucket)
        per_page = 6
        page = max(0, min(page, max(0, (len(resources) - 1) // per_page)))
        start = page * per_page
        subset = resources[start : start + per_page]
        protected_pages = bucket == "p" and any(r.metadata.get("raw_downloadable") is False for r in resources)

        lines = [
            f"{_BUCKET_NAMES.get(bucket, '📦 Recursos')}",
            f"<b>{len(resources)}</b> item(ns)",
            "",
        ]
        rows = []

        if bucket == "p" and resources:
            if protected_pages:
                lines.extend([
                    "🔒 <b>Leitor protegido</b>",
                    "As páginas foram identificadas, mas este leitor não entrega imagens brutas válidas para exportação.",
                    "",
                ])
            else:
                rows.append([
                    InlineKeyboardButton("📕 Gerar PDF", callback_data=f"bundle:pdf:{bucket}:{key}"),
                    InlineKeyboardButton("🗜️ Gerar ZIP", callback_data=f"bundle:zip:{bucket}:{key}"),
                ])
                rows.append([
                    InlineKeyboardButton("📤 Enviar páginas", callback_data=f"all:{bucket}:{key}:file")
                ])
        elif bucket == "i" and resources:
            rows.append([
                InlineKeyboardButton("🗜️ Baixar imagens em ZIP", callback_data=f"bundle:zip:{bucket}:{key}")
            ])

        for offset, resource in enumerate(subset, start=start):
            label = resource.title or unquote(Path(urlsplit(resource.url).path).name) or resource.type.value
            suffix = ""
            quality = resource.quality or (f"{resource.height}p" if resource.height else None)
            if quality:
                suffix += f" • {quality}"
            if resource.drm:
                suffix += " • 🔒 DRM"
            lines.append(f"<b>{offset + 1}.</b> {_safe(label, 58)}{_safe(suffix, 30)}")
            if bucket == "v" and not resource.drm:
                rows.append([
                    InlineKeyboardButton(
                        f"⬇️ Baixar {offset + 1}",
                        callback_data=f"mode:{bucket}:{key}:{offset}",
                    )
                ])
            elif bucket not in {"p", "i"} and not resource.drm:
                rows.append([
                    InlineKeyboardButton(
                        f"⬇️ Baixar {offset + 1}",
                        callback_data=f"one:{bucket}:{key}:{offset}:file",
                    )
                ])

        if bucket not in {"p", "i"} and len(resources) > 1 and any(not r.drm for r in resources):
            rows.append([
                InlineKeyboardButton(
                    f"📥 Baixar todos ({sum(not r.drm for r in resources)})",
                    callback_data=f"all:{bucket}:{key}:file",
                )
            ])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("‹ Anterior", callback_data=f"cat:{bucket}:{key}:{page - 1}"))
        if start + per_page < len(resources):
            nav.append(InlineKeyboardButton("Próxima ›", callback_data=f"cat:{bucket}:{key}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"home:x:{key}:0")])
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
                    markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✖️ Cancelar", callback_data=f"cancel:{job.id}")]
                    ])
                try:
                    await message.edit_text(text, reply_markup=markup)
                except Exception:
                    pass
                last = text

            if job.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
                paths = _all_completed_paths(job)
                failed = [item for item in job.items if item.state == JobState.FAILED]
                delivered: list[Path] = []
                delivery_error: Exception | None = None

                if paths and job.state != JobState.CANCELLED:
                    try:
                        async def upload_path_to_telegram(path: Path, *, as_video: bool, caption: str):
                            total_size = path.stat().st_size
                            upload_last_at = time.monotonic()
                            upload_last_bytes = 0

                            try:
                                await message.edit_text(
                                    upload_progress_text(path.name, 0, total_size, 0.0)
                                )
                            except Exception:
                                pass

                            async def on_upload(sent: int, total: int):
                                nonlocal upload_last_at, upload_last_bytes
                                now_upload = time.monotonic()
                                if sent < total and now_upload - upload_last_at < 0.7:
                                    return
                                elapsed = max(now_upload - upload_last_at, 0.001)
                                upload_speed = max(0.0, (sent - upload_last_bytes) / elapsed)
                                upload_last_at = now_upload
                                upload_last_bytes = sent
                                try:
                                    await message.edit_text(
                                        upload_progress_text(
                                            path.name,
                                            sent,
                                            total or total_size,
                                            upload_speed,
                                        )
                                    )
                                except Exception:
                                    pass

                            return await delivery.send_path(
                                message,
                                path,
                                as_video=as_video,
                                caption=caption,
                                progress_callback=on_upload,
                            )

                        if bundle_mode == "pdf":
                            out = settings.downloads_dir / f"iris-{job.id}.pdf"
                            build_pdf(paths, out)
                            await upload_path_to_telegram(
                                out,
                                as_video=False,
                                caption=f"📕 IRIS • {len(paths)} página(s)",
                            )
                            delivered = paths + [out]
                        elif bundle_mode == "zip":
                            out = settings.downloads_dir / f"iris-{job.id}.zip"
                            build_zip(paths, out)
                            await upload_path_to_telegram(
                                out,
                                as_video=False,
                                caption=f"🗜️ IRIS • {len(paths)} arquivo(s)",
                            )
                            delivered = paths + [out]
                        else:
                            completed_resources = [
                                resource
                                for item, resource in zip(job.items, jobs.resources.get(job_id, []))
                                if item.state == JobState.COMPLETED and item.output_path
                            ]
                            for index, path in enumerate(paths):
                                resource = completed_resources[index] if index < len(completed_resources) else None
                                await upload_path_to_telegram(
                                    path,
                                    as_video=delivery_mode == "video",
                                    caption=delivery_caption(resource, as_video=delivery_mode == "video"),
                                )
                            delivered = paths
                    except Exception as exc:
                        delivery_error = exc

                try:
                    if delivery_error is not None:
                        await message.edit_text(
                            "⚠️ <b>Falha na entrega</b>\n\n"
                            f"<code>{_safe(str(delivery_error), 220)}</code>"
                        )
                    elif failed:
                        await message.edit_text(
                            "⚠️ <b>Concluído parcialmente</b>\n\n"
                            f"✅ {len(paths)} concluído(s)\n"
                            f"❌ {len(failed)} falha(s)"
                        )
                    elif job.state == JobState.CANCELLED:
                        await message.edit_text("✖️ <b>Download cancelado.</b>")
                    else:
                        await message.edit_text(
                            "✅ <b>Concluído</b>\n\n"
                            f"📦 {len(paths)} arquivo(s)"
                        )
                except Exception:
                    pass

                await delivery.cleanup(delivered)
                return

            await asyncio.sleep(1.2)

    async def launch_download(query, selected: list[MediaResource], *, delivery_mode: str = "file", bundle_mode: str | None = None):
        selected = [
            resource
            for resource in selected
            if not resource.drm and resource.metadata.get("raw_downloadable") is not False
        ]
        if not selected:
            await query.edit_message_text(
                "🔒 <b>Indisponível para download</b>\n\n"
                "Este conteúdo foi detectado, mas está protegido ou não é exportável como arquivo bruto."
            )
            return

        # Fastest possible route: for a single direct resource, ask Telegram to
        # fetch the URL itself. This avoids the Railway download + Telegram
        # re-upload round-trip entirely. Any failure falls back transparently.
        if len(selected) == 1 and bundle_mode is None:
            resource = selected[0]
            try:
                sent = await delivery.send_remote_resource(
                    query.message,
                    resource,
                    as_video=delivery_mode == "video",
                    caption=delivery_caption(resource, as_video=delivery_mode == "video"),
                )
                if sent:
                    await query.edit_message_text(
                        "⚡ <b>Enviado em modo rápido</b>\n\n"
                        "O Telegram buscou o arquivo direto da origem, sem reupload pela Railway."
                    )
                    return
            except Exception:
                pass

        job = jobs.create(selected)
        jobs.launch(job.id)
        await query.edit_message_text(progress_text(job))
        asyncio.create_task(
            watch_job(
                query.message,
                job.id,
                delivery_mode=delivery_mode,
                bundle_mode=bundle_mode,
            )
        )

    async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        uid = query.from_user.id if query.from_user else None
        if not _can_use(uid):
            return

        parts = (query.data or "").split(":")
        action = parts[0] if parts else ""

        if action == "cancel" and len(parts) >= 2:
            jobs.cancel(parts[1])
            job = jobs.get(parts[1])
            if job:
                await query.edit_message_text("✖️ <b>Download cancelado.</b>")
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
                await query.edit_message_text("⌛ <b>Análise expirada.</b>\n\nEnvie a URL novamente.")
                return
            started = time.monotonic()
            await query.edit_message_text(
                "🔬 <b>Análise profunda</b>\n\n"
                "🌐 Observando rede, player e conteúdo carregado dinamicamente…"
            )
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
            if result:
                await show_category(query, result, key, bucket, int(page_raw))
            return

        if action == "mode" and len(parts) >= 4:
            bucket, key, index_raw = parts[1], parts[2], parts[3]
            rows = [[
                InlineKeyboardButton("🎬 Enviar como vídeo", callback_data=f"one:{bucket}:{key}:{index_raw}:video"),
                InlineKeyboardButton("📦 Enviar como arquivo", callback_data=f"one:{bucket}:{key}:{index_raw}:file"),
            ], [
                InlineKeyboardButton("↩️ Voltar", callback_data=f"cat:{bucket}:{key}:0")
            ]]
            await query.edit_message_text(
                "📤 <b>Como você quer receber?</b>",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return

        if action == "bundle" and len(parts) >= 4:
            bundle_mode, bucket, key = parts[1], parts[2], parts[3]
            result = _ANALYSES.get(key)
            if result:
                await launch_download(
                    query,
                    resources_for_bucket(result, bucket),
                    bundle_mode=bundle_mode,
                )
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ajuda", help_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("status", admin_status))
    application.add_handler(CommandHandler("downloads", admin_jobs))
    application.add_handler(CommandHandler("jobs", admin_jobs))
    application.add_handler(CommandHandler("conta06", admin_userbot))
    application.add_handler(CommandHandler("diagnostico", admin_diag))
    application.add_handler(CommandHandler("limpar", admin_clean))
    application.add_handler(CommandHandler("fila", admin_queue))
    application.add_handler(CommandHandler("retomar", admin_queue))
    application.add_handler(CallbackQueryHandler(callbacks))

    media_filter = (
        filters.VIDEO
        | filters.VIDEO_NOTE
        | filters.AUDIO
        | filters.VOICE
        | filters.Document.ALL
        | filters.PHOTO
        | filters.ANIMATION
    )
    application.add_handler(MessageHandler(media_filter, on_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    await application.initialize()

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Abrir o Iris"),
            BotCommand("ajuda", "Como usar"),
        ],
        scope=BotCommandScopeDefault(),
    )

    if settings.admin_id:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Abrir o Iris"),
                BotCommand("ajuda", "Como usar"),
                BotCommand("status", "Status administrativo"),
                BotCommand("downloads", "Downloads recentes"),
                BotCommand("conta06", "Conectar a Conta 06"),
                BotCommand("diagnostico", "Testar entregas"),
                BotCommand("limpar", "Limpar temporários"),
                BotCommand("fila", "Fila persistente do site"),
                BotCommand("retomar", "Continuar fila de onde parou"),
            ],
            scope=BotCommandScopeChat(chat_id=settings.admin_id),
        )

    try:
        await application.bot.set_my_description(
            "🔎 Encontre vídeos, imagens, páginas, áudios e arquivos em links. Downloads em lote, PDF, ZIP e muito mais."
        )
        await application.bot.set_my_short_description(
            "🔎 Analise links e organize downloads de mídia."
        )
    except Exception:
        pass

    await application.start()
    await application.updater.start_polling(drop_pending_updates=False)

    if settings.auto_userbot_login and settings.admin_id and userbot.configured:
        try:
            if not await userbot.is_authorized():
                result = await userbot.begin_login()
                if result == "code":
                    _AUTH_STAGE = "code"
                    await application.bot.send_message(
                        settings.admin_id,
                        "📲 <b>Conta 06</b>\n\n"
                        "O código de acesso foi solicitado automaticamente.\n"
                        "Envie <b>somente o código</b> aqui para concluir a conexão.\n\n"
                        "🔐 Sua mensagem será apagada após a leitura.",
                    )
        except Exception as exc:
            await application.bot.send_message(
                settings.admin_id,
                "⚠️ <b>Conta 06</b>\n\n"
                f"Não consegui iniciar o login automaticamente: <code>{_safe(str(exc), 160)}</code>",
            )

    try:
        queue_state = site_queue.status()
        if (
            settings.site_queue_auto_start
            and settings.admin_id
            and not queue_state.get("last_started_at")
        ):
            stats = await site_queue.discover()
            await site_queue.start(application.bot, settings.admin_id, discover=False)
            await application.bot.send_message(
                settings.admin_id,
                "▶️ <b>Teste do catálogo iniciado</b>\n\n"
                f"🎞️ Itens registrados: <b>{stats['total']}</b>\n"
                "Vou enviar um por um e salvar o progresso automaticamente.",
            )
        else:
            resumed = await site_queue.maybe_resume(application.bot)
            if resumed and settings.admin_id:
                await application.bot.send_message(
                    settings.admin_id,
                    "♻️ <b>Fila retomada automaticamente</b>\n\n"
                    "Continuando do ponto salvo antes do reinício.",
                )
    except Exception as exc:
        print(f"IRIS_QUEUE_RESUME_ERROR {type(exc).__name__}: {exc}", flush=True)

    if settings.run_benchmark and settings.admin_id:
        async def _benchmark_once():
            await asyncio.sleep(3)
            try:
                await application.bot.send_message(
                    settings.admin_id,
                    "🧪 <b>Benchmark do Iris</b>\n\n"
                    "Medindo download da Railway, upload MTProto da Conta 06 e entrega direta por URL…",
                )
                results = await run_admin_benchmark(application.bot, settings.admin_id)
                for line in results:
                    print("IRIS_BENCHMARK " + line, flush=True)
                await application.bot.send_message(
                    settings.admin_id,
                    "📊 <b>Resultado do benchmark</b>\n\n" + "\n".join(results),
                )
            except Exception as exc:
                await application.bot.send_message(
                    settings.admin_id,
                    f"⚠️ <b>Benchmark falhou</b>\n\n<code>{_safe(str(exc), 220)}</code>",
                )
        asyncio.create_task(_benchmark_once(), name="iris-admin-benchmark")

    if settings.run_video_smoke and settings.admin_id:
        async def _video_smoke_once():
            await asyncio.sleep(4)
            try:
                await run_native_video_smoke(application.bot, settings.admin_id)
                print("IRIS_VIDEO_SMOKE_SENT", flush=True)
            except Exception as exc:
                print(f"IRIS_VIDEO_SMOKE_ERROR {type(exc).__name__}: {exc}", flush=True)
        asyncio.create_task(_video_smoke_once(), name="iris-native-video-smoke")

    if settings.run_large_video_smoke and settings.admin_id:
        async def _large_video_smoke_once():
            await asyncio.sleep(4)
            try:
                result = await run_large_video_smoke(application.bot, settings.admin_id)
                print(
                    "IRIS_LARGE_VIDEO_SMOKE "
                    + " ".join(f"{key}={value}" for key, value in result.items()),
                    flush=True,
                )
            except Exception as exc:
                print(f"IRIS_LARGE_VIDEO_SMOKE_ERROR {type(exc).__name__}: {exc}", flush=True)
        asyncio.create_task(_large_video_smoke_once(), name="iris-large-video-smoke")

    if settings.run_source_speed_smoke:
        async def _source_speed_once():
            await asyncio.sleep(4)
            try:
                rows = await run_source_speed_smoke()
                for row in rows:
                    print("IRIS_SOURCE_SPEED " + row, flush=True)
            except Exception as exc:
                print(f"IRIS_SOURCE_SPEED_ERROR {type(exc).__name__}: {exc}", flush=True)
        asyncio.create_task(_source_speed_once(), name="iris-source-speed-smoke")

    if settings.run_mtproto_speed_smoke:
        async def _mtproto_speed_once():
            await asyncio.sleep(4)
            try:
                rows = await run_mtproto_speed_smoke(application.bot)
                for row in rows:
                    print("IRIS_MTPROTO_SPEED " + row, flush=True)
            except Exception as exc:
                print(f"IRIS_MTPROTO_SPEED_ERROR {type(exc).__name__}: {exc}", flush=True)
        asyncio.create_task(_mtproto_speed_once(), name="iris-mtproto-speed-smoke")

    try:
        await asyncio.Event().wait()
    finally:
        if site_queue.task and not site_queue.task.done():
            site_queue.task.cancel()
            try:
                await site_queue.task
            except asyncio.CancelledError:
                pass
        await userbot.close()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
