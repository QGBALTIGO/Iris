from app.bot import deliverable_paths, progress_text, resources_for_bucket
from app.models import AnalyzeResult, DownloadItemStatus, DownloadJob, JobState, MediaResource, ResourceType


def test_video_bucket_includes_video_and_playlist_only():
    result = AnalyzeResult(
        url="https://example.com",
        final_url="https://example.com",
        resources=[
            MediaResource(url="https://example.com/a.mp4", type=ResourceType.VIDEO),
            MediaResource(url="https://example.com/a.m3u8", type=ResourceType.PLAYLIST),
            MediaResource(url="https://example.com/a.jpg", type=ResourceType.IMAGE),
        ],
    )
    assert len(resources_for_bucket(result, "v")) == 2
    assert len(resources_for_bucket(result, "i")) == 1


def test_progress_text_aggregates_items():
    job = DownloadJob(
        id="abc",
        state=JobState.RUNNING,
        created_at=0,
        updated_at=0,
        items=[
            DownloadItemStatus(url="https://x/a.mp4", state=JobState.COMPLETED, progress=1),
            DownloadItemStatus(url="https://x/b.mp4", state=JobState.RUNNING, progress=0.5),
        ],
    )
    text = progress_text(job)
    assert "75%" in text
    assert "Concluídos: 1/2" in text


def test_deliverable_paths_separates_telegram_sized_files(tmp_path):
    small = tmp_path / "small.mp4"
    big = tmp_path / "big.mp4"
    small.write_bytes(b"x" * 10)
    big.write_bytes(b"x" * 100)
    job = DownloadJob(
        id="files",
        state=JobState.COMPLETED,
        created_at=0,
        updated_at=0,
        items=[
            DownloadItemStatus(url="https://x/small.mp4", state=JobState.COMPLETED, progress=1, output_path=str(small)),
            DownloadItemStatus(url="https://x/big.mp4", state=JobState.COMPLETED, progress=1, output_path=str(big)),
        ],
    )
    sendable, oversized = deliverable_paths(job, 50)
    assert sendable == [small]
    assert oversized == [big]
