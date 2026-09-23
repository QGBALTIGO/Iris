from pathlib import Path

from app.popular_queue import PopularQueueManager


def test_popular_queue_persists_candidates_and_alternates(tmp_path: Path):
    manager = PopularQueueManager(tmp_path / "popular.sqlite3")

    first = manager._persist_candidates(
        "tubepussy",
        [
            "https://tubepussy.org/most-popular/",
            "https://tubepussy.org/video-a/",
            "https://tubepussy.org/video-b/",
            "https://tubepussy.org/video-a/",
        ],
    )
    second = manager._persist_candidates(
        "xvideosputaria",
        [
            "https://xvideosputaria.com/mais-populares/#forward",
            "https://xvideosputaria.com/video-x/",
            "https://xvideosputaria.com/video-y/",
        ],
    )

    assert first == {"found": 2, "added": 2}
    assert second == {"found": 2, "added": 2}
    assert manager._pending_count("tubepussy") == 2
    assert manager._pending_count("xvideosputaria") == 2

    tube = manager._next_item("tubepussy")
    xvp = manager._next_item("xvideosputaria")

    assert tube is not None
    assert tube.source == "tubepussy"
    assert tube.page_url.endswith("/video-a/")
    assert xvp is not None
    assert xvp.source == "xvideosputaria"
    assert xvp.page_url.endswith("/video-x/")

    manager._set_next_source("xvideosputaria")
    assert manager.status()["next_source"] == "xvideosputaria"


def test_popular_queue_does_not_readd_sent_item(tmp_path: Path):
    manager = PopularQueueManager(tmp_path / "popular.sqlite3")
    manager._persist_candidates(
        "tubepussy",
        ["https://tubepussy.org/video-a/"],
    )
    item = manager._next_item("tubepussy")
    assert item is not None

    manager._mark_sent(
        item,
        title="Video A",
        media_key="cdn.example/video-a.mp4",
        fingerprint="abc123",
        message_id=10,
    )

    result = manager._persist_candidates(
        "tubepussy",
        ["https://tubepussy.org/video-a/"],
    )
    assert result["added"] == 0
    assert manager._pending_count("tubepussy") == 0
    assert manager._already_sent("cdn.example/video-a.mp4", "other")
    assert manager._already_sent("other", "abc123")
