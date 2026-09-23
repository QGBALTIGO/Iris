from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_or_none(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value else None


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str | None = os.getenv("IRIS_BOT_TOKEN")
    admin_id: int | None = _int_or_none("IRIS_ADMIN_ID")

    telegram_api_id: int | None = _int_or_none("IRIS_TELEGRAM_API_ID")
    telegram_api_hash: str | None = os.getenv("IRIS_TELEGRAM_API_HASH")
    telegram_phone: str | None = os.getenv("IRIS_TELEGRAM_PHONE")
    telegram_session_path: str = os.getenv("IRIS_TELEGRAM_SESSION_PATH", "/data/account06")
    userbot_threshold_bytes: int = int(os.getenv("IRIS_USERBOT_THRESHOLD_BYTES", str(20 * 1024 * 1024)))
    auto_userbot_login: bool = _bool("IRIS_AUTO_USERBOT_LOGIN", False)

    public_enabled: bool = _bool("IRIS_PUBLIC_ENABLED", False)
    browser_enabled: bool = _bool("IRIS_BROWSER_ENABLED", True)
    browser_disable_gpu: bool = _bool("IRIS_BROWSER_DISABLE_GPU", False)
    ytdlp_enabled: bool = _bool("IRIS_YTDLP_ENABLED", True)
    max_redirects: int = int(os.getenv("IRIS_MAX_REDIRECTS", "5"))
    request_timeout: float = float(os.getenv("IRIS_REQUEST_TIMEOUT", "20"))
    max_html_bytes: int = int(os.getenv("IRIS_MAX_HTML_BYTES", str(5 * 1024 * 1024)))
    max_download_bytes: int = int(os.getenv("IRIS_MAX_DOWNLOAD_BYTES", str(8 * 1024 * 1024 * 1024)))
    max_manifest_probes: int = int(os.getenv("IRIS_MAX_MANIFEST_PROBES", "12"))
    max_browser_requests: int = int(os.getenv("IRIS_MAX_BROWSER_REQUESTS", "1600"))
    download_concurrency: int = int(os.getenv("IRIS_DOWNLOAD_CONCURRENCY", "6"))
    downloads_dir: Path = Path(os.getenv("IRIS_DOWNLOADS_DIR", "downloads"))
    bot_upload_limit_bytes: int = int(os.getenv("IRIS_BOT_UPLOAD_LIMIT_BYTES", str(45 * 1024 * 1024)))
    cleanup_after_delivery: bool = _bool("IRIS_CLEANUP_AFTER_DELIVERY", True)
    notify_startup: bool = _bool("IRIS_NOTIFY_STARTUP", False)
    run_benchmark: bool = _bool("IRIS_RUN_BENCHMARK", False)
    run_video_smoke: bool = _bool("IRIS_RUN_VIDEO_SMOKE", False)
    run_large_video_smoke: bool = _bool("IRIS_RUN_LARGE_VIDEO_SMOKE", False)
    run_source_speed_smoke: bool = _bool("IRIS_RUN_SOURCE_SPEED_SMOKE", False)
    run_mtproto_speed_smoke: bool = _bool("IRIS_RUN_MTPROTO_SPEED_SMOKE", False)
    run_admin_test_suite: bool = _bool("IRIS_RUN_ADMIN_TEST_SUITE", False)
    admin_test_nonce: str | None = os.getenv("IRIS_ADMIN_TEST_NONCE")
    site_queue_auto_start: bool = _bool("IRIS_SITE_QUEUE_AUTO_START", False)
    queue_recovery_nonce: str | None = os.getenv("IRIS_QUEUE_RECOVERY_NONCE")
    one_shot_url: str | None = os.getenv("IRIS_ONE_SHOT_URL")
    one_shot_nonce: str | None = os.getenv("IRIS_ONE_SHOT_NONCE")


settings = Settings()
