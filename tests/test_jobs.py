from pathlib import Path
import asyncio
import pytest

from app.jobs import JobStore
from app.models import JobState, MediaResource, ResourceType


class FakeEngine:
    async def download(self, resource, filename=None, progress=None):
        if "fail" in resource.url:
            raise RuntimeError("boom")
        if progress:
            await progress(50, 100, 0.5)
            await progress(100, 100, 1.0)
        return Path("/tmp/done.mp4")


@pytest.mark.asyncio
async def test_batch_success():
    store = JobStore(engine=FakeEngine(), concurrency=2)
    job = store.create([MediaResource(url="https://example.com/a.mp4", type=ResourceType.VIDEO), MediaResource(url="https://example.com/b.mp4", type=ResourceType.VIDEO)])
    result = await store.run(job.id)
    assert result.state == JobState.COMPLETED
    assert all(item.progress == 1 for item in result.items)


@pytest.mark.asyncio
async def test_batch_partial_failure_marks_job_failed():
    store = JobStore(engine=FakeEngine(), concurrency=2)
    job = store.create([MediaResource(url="https://example.com/a.mp4", type=ResourceType.VIDEO), MediaResource(url="https://example.com/fail.mp4", type=ResourceType.VIDEO)])
    result = await store.run(job.id)
    assert result.state == JobState.FAILED
    assert result.items[1].state == JobState.FAILED


class SlowEngine:
    async def download(self, resource, filename=None, progress=None):
        await asyncio.sleep(5)
        return Path("/tmp/slow.mp4")


@pytest.mark.asyncio
async def test_job_can_be_cancelled():
    import asyncio
    store = JobStore(engine=SlowEngine(), concurrency=1)
    job = store.create([MediaResource(url="https://example.com/slow.mp4", type=ResourceType.VIDEO)])
    task = store.launch(job.id)
    await asyncio.sleep(0)
    assert store.cancel(job.id) is True
    result = await task
    assert result.state == JobState.CANCELLED
    assert result.items[0].state == JobState.CANCELLED
