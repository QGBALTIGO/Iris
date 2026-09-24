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


def test_queue_rejects_taxonomy_and_archive_urls(tmp_path: Path):
    manager = SiteQueueManager(tmp_path / "queue.sqlite3")

    assert manager._is_content_post_url(
        "https://pornocomlegenda.blog/um-post-real/"
    )
    assert not manager._is_content_post_url(
        "https://pornocomlegenda.blog/tag/sexo-com-patrao/"
    )
    assert not manager._is_content_post_url(
        "https://pornocomlegenda.blog/category/legendados/"
    )
    assert not manager._is_content_post_url(
        "https://pornocomlegenda.blog/author/admin/"
    )
    assert not manager._is_content_post_url(
        "https://pornocomlegenda.blog/feed/"
    )

    assert manager._is_post_sitemap_url(
        "https://pornocomlegenda.blog/wp-sitemap-posts-post-1.xml"
    )
    assert manager._is_post_sitemap_url(
        "https://pornocomlegenda.blog/post-sitemap.xml"
    )
    assert not manager._is_post_sitemap_url(
        "https://pornocomlegenda.blog/wp-sitemap-taxonomies-post_tag-1.xml"
    )


def test_queue_prunes_invalid_pending_entries_but_preserves_sent(tmp_path: Path):
    manager = SiteQueueManager(tmp_path / "queue.sqlite3")
    now = time.time()

    with manager._connect() as db:
        for url, status in (
            ("https://pornocomlegenda.blog/post-valido/", "pending"),
            ("https://pornocomlegenda.blog/tag/invalida/", "pending"),
            ("https://pornocomlegenda.blog/category/invalida/", "failed"),
            ("https://pornocomlegenda.blog/tag/ja-enviada/", "sent"),
        ):
            db.execute(
                """
                INSERT INTO queue_items(
                    site, post_id, url, title, published_at, status,
                    attempts, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    "https://pornocomlegenda.blog",
                    url,
                    url,
                    "Teste",
                    "2026-09-24T00:00:00",
                    status,
                    now,
                    now,
                ),
            )
        db.commit()

    assert manager.prune_invalid_entries() == 2

    with manager._connect() as db:
        rows = db.execute(
            "SELECT url, status FROM queue_items ORDER BY url"
        ).fetchall()

    kept = {(row["url"], row["status"]) for row in rows}
    assert (
        "https://pornocomlegenda.blog/post-valido/",
        "pending",
    ) in kept
    assert (
        "https://pornocomlegenda.blog/tag/ja-enviada/",
        "sent",
    ) in kept
    assert all(
        "/tag/invalida/" not in url and "/category/invalida/" not in url
        for url, _ in kept
    )


def test_recover_failed_resets_attempt_counter(tmp_path: Path):
    manager = SiteQueueManager(tmp_path / "queue.sqlite3")
    now = time.time()
    with manager._connect() as db:
        db.execute(
            """
            INSERT INTO queue_items(
                site, post_id, url, title, published_at, status,
                attempts, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, 'failed', 3, ?, ?)
            """,
            (
                "https://pornocomlegenda.blog",
                "recover-1",
                "https://pornocomlegenda.blog/post-recuperavel/",
                "Recuperável",
                "2026-09-24T00:00:00",
                now,
                now,
            ),
        )
        db.commit()

    assert manager.recover_failed_once("nonce-test") == 1

    with manager._connect() as db:
        row = db.execute(
            "SELECT status, attempts FROM queue_items WHERE post_id='recover-1'"
        ).fetchone()

    assert row["status"] == "pending"
    assert row["attempts"] == 0
