import time
from pathlib import Path

from app.site_queue import SiteQueueManager


def test_queue_persists_and_resumes_interrupted_items(tmp_path: Path):
    manager = SiteQueueManager(tmp_path / "queue.sqlite3")
    now = time.time()
    with manager._connect() as db:
        db.execute(
            """
            INSERT INTO queue_items(
                site, post_id, url, title, published_at, status,
                attempts, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, 'processing', 1, ?, ?)
            """,
            (
                "https://pornocomlegenda.blog",
                "1",
                "https://pornocomlegenda.blog/teste/",
                "Teste",
                "2026-09-22T00:00:00",
                now,
                now,
            ),
        )
        db.commit()

    assert manager.reset_stale(0) == 1
    with manager._connect() as db:
        row = db.execute("SELECT status FROM queue_items").fetchone()
    assert row["status"] == "pending"


def test_queue_retries_non_video_relay_and_marks_video_sent(tmp_path: Path):
    manager = SiteQueueManager(tmp_path / "queue.sqlite3")
    now = time.time()
    with manager._connect() as db:
        cur = db.execute(
            """
            INSERT INTO queue_items(
                site, post_id, url, title, published_at, status,
                attempts, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, 'awaiting_delivery', 1, ?, ?)
            """,
            (
                "https://pornocomlegenda.blog",
                "1",
                "https://pornocomlegenda.blog/teste/",
                "Teste",
                "2026-09-22T00:00:00",
                now,
                now,
            ),
        )
        item_id = cur.lastrowid
        db.commit()

    manager.mark_relay_result(item_id, media_kind="document")
    with manager._connect() as db:
        row = db.execute("SELECT status FROM queue_items WHERE id=?", (item_id,)).fetchone()
    assert row["status"] == "retry_local"

    manager.mark_relay_result(item_id, media_kind="video", message_id=123)
    with manager._connect() as db:
        row = db.execute(
            "SELECT status, sent_message_id, sent_at FROM queue_items WHERE id=?",
            (item_id,),
        ).fetchone()
    assert row["status"] == "sent"
    assert row["sent_message_id"] == 123
    assert row["sent_at"] is not None
