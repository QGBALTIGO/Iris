from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class ResourceType(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    ARCHIVE = "archive"
    SUBTITLE = "subtitle"
    STREAM = "stream"
    PLAYLIST = "playlist"
    OTHER = "other"


class MediaVariant(BaseModel):
    url: str
    label: str | None = None
    width: int | None = None
    height: int | None = None
    bandwidth: int | None = None
    codecs: str | None = None
    mime_type: str | None = None


class MediaResource(BaseModel):
    url: str
    type: ResourceType = ResourceType.OTHER
    source: str = "html"
    title: str | None = None
    mime_type: str | None = None
    size: int | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    quality: str | None = None
    codecs: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    variants: list[MediaVariant] = Field(default_factory=list)
    encrypted: bool = False
    drm: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    url: HttpUrl
    deep: bool = False


class AnalyzeResult(BaseModel):
    url: str
    final_url: str
    title: str | None = None
    content_type: str | None = None
    service: str | None = None
    drm_systems: list[str] = Field(default_factory=list)
    resources: list[MediaResource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.resources:
            result[item.type.value] = result.get(item.type.value, 0) + 1
        return result


class BatchDownloadRequest(BaseModel):
    resources: list[MediaResource]


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadItemStatus(BaseModel):
    url: str
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    bytes_downloaded: int = 0
    total_bytes: int | None = None
    output_path: str | None = None
    error: str | None = None


class DownloadJob(BaseModel):
    id: str
    state: JobState = JobState.QUEUED
    items: list[DownloadItemStatus] = Field(default_factory=list)
    created_at: float
    updated_at: float
