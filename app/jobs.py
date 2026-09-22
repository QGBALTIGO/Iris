from __future__ import annotations

import asyncio
import time
import uuid

from app.downloads import DownloadEngine
from app.models import DownloadItemStatus, DownloadJob, JobState, MediaResource


class JobStore:
    def __init__(self, engine: DownloadEngine | None = None, concurrency: int = 2):
        self.engine = engine or DownloadEngine()
        self.jobs: dict[str, DownloadJob] = {}
        self.resources: dict[str, list[MediaResource]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(concurrency)

    def create(self, resources: list[MediaResource]) -> DownloadJob:
        now = time.time()
        job_id = uuid.uuid4().hex[:12]
        job = DownloadJob(
            id=job_id,
            state=JobState.QUEUED,
            items=[DownloadItemStatus(url=r.url) for r in resources],
            created_at=now,
            updated_at=now,
        )
        self.jobs[job_id] = job
        self.resources[job_id] = resources
        return job

    def launch(self, job_id: str) -> asyncio.Task:
        task = asyncio.create_task(self.run(job_id), name=f"iris-job-{job_id}")
        self.tasks[job_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(job_id, None))
        return task

    async def run(self, job_id: str) -> DownloadJob:
        job = self.jobs[job_id]
        resources = self.resources[job_id]
        job.state = JobState.RUNNING
        job.updated_at = time.time()

        async def one(index: int, resource: MediaResource):
            item = job.items[index]
            item.state = JobState.RUNNING

            async def progress(downloaded, total, value):
                item.bytes_downloaded = downloaded
                item.total_bytes = total
                item.progress = value
                job.updated_at = time.time()

            try:
                async with self.semaphore:
                    output = await self.engine.download(resource, progress=progress)
                item.output_path = str(output)
                item.progress = 1.0
                item.state = JobState.COMPLETED
            except asyncio.CancelledError:
                item.state = JobState.CANCELLED
                raise
            except Exception as exc:
                item.state = JobState.FAILED
                item.error = str(exc)[:500]
            finally:
                job.updated_at = time.time()

        try:
            await asyncio.gather(*(one(i, resource) for i, resource in enumerate(resources)))
        except asyncio.CancelledError:
            job.state = JobState.CANCELLED
            for item in job.items:
                if item.state in {JobState.QUEUED, JobState.RUNNING}:
                    item.state = JobState.CANCELLED
            job.updated_at = time.time()
            return job

        job.state = JobState.COMPLETED if all(i.state == JobState.COMPLETED for i in job.items) else JobState.FAILED
        job.updated_at = time.time()
        return job

    def cancel(self, job_id: str) -> bool:
        task = self.tasks.get(job_id)
        job = self.jobs.get(job_id)
        if not task or not job or task.done():
            return False
        task.cancel()
        job.state = JobState.CANCELLED
        job.updated_at = time.time()
        return True

    def get(self, job_id: str) -> DownloadJob | None:
        return self.jobs.get(job_id)

    def list(self) -> list[DownloadJob]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)
