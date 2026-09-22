from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str | None = os.getenv("IRIS_BOT_TOKEN")
    admin_id: int | None = int(os.getenv("IRIS_ADMIN_ID")) if os.getenv("IRIS_ADMIN_ID") else None
    browser_enabled: bool = _bool("IRIS_BROWSER_ENABLED", True)
    ytdlp_enabled: bool = _bool("IRIS_YTDLP_ENABLED", True)
    max_redirects: int = int(os.getenv("IRIS_MAX_REDIRECTS", "5"))
    request_timeout: float = float(os.getenv("IRIS_REQUEST_TIMEOUT", "20"))
    max_html_bytes: int = int(os.getenv("IRIS_MAX_HTML_BYTES", str(5 * 1024 * 1024)))
    max_download_bytes: int = int(os.getenv("IRIS_MAX_DOWNLOAD_BYTES", str(8 * 1024 * 1024 * 1024)))
    max_manifest_probes: int = int(os.getenv("IRIS_MAX_MANIFEST_PROBES", "12"))
    max_browser_requests: int = int(os.getenv("IRIS_MAX_BROWSER_REQUESTS", "1200"))
    downloads_dir: Path = Path(os.getenv("IRIS_DOWNLOADS_DIR", "downloads"))
    bot_upload_limit_bytes: int = int(os.getenv("IRIS_BOT_UPLOAD_LIMIT_BYTES", str(45 * 1024 * 1024)))


settings = Settings()
