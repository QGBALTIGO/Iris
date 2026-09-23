import asyncio
from types import SimpleNamespace

import pytest

from app.userbot import UserbotManager


@pytest.mark.asyncio
async def test_delivery_channel_forwards_html_parse_mode(monkeypatch):
    manager = UserbotManager()
    manager._lock = asyncio.Lock()
    calls = {}

    async def resolve(invite_url):
        assert invite_url == "https://t.me/+example"
        return "target"

    async def send_file(target, file_or_url, **kwargs):
        calls["target"] = target
        calls["file"] = file_or_url
        calls["kwargs"] = kwargs
        return SimpleNamespace(id=99)

    monkeypatch.setattr(manager, "resolve_delivery_target", resolve)
    monkeypatch.setattr(manager, "_send_file_to_entity", send_file)

    await manager.send_to_delivery_channel(
        "https://t.me/+example",
        "/tmp/video.mp4",
        caption="<b>🚫 Teste</b>",
        as_video=True,
        parse_mode="html",
    )

    assert calls["kwargs"]["parse_mode"] == "html"


@pytest.mark.asyncio
async def test_repair_reparses_visible_html(monkeypatch):
    manager = UserbotManager()
    manager._lock = asyncio.Lock()
    edited = []

    messages = [
        SimpleNamespace(
            id=5,
            message=(
                "<b>🚫 Larissa Sumpani</b>\n\n"
                "<blockquote expandable>🔎 Tags: #Teste</blockquote>"
            ),
        ),
        SimpleNamespace(id=6, message="Legenda normal"),
    ]

    class FakeClient:
        def iter_messages(self, target, limit=1000):
            async def gen():
                for item in messages:
                    yield item
            return gen()

        async def edit_message(self, target, message_id, text, parse_mode=None):
            edited.append((target, message_id, text, parse_mode))

    async def fake_client():
        return FakeClient()

    async def resolve(invite_url):
        return "popular-channel"

    monkeypatch.setattr(manager, "client", fake_client)
    monkeypatch.setattr(manager, "resolve_delivery_target", resolve)

    result = await manager.repair_delivery_channel_captions(
        "https://t.me/+example",
        limit=100,
    )

    assert result == {"scanned": 2, "edited": 1, "failed": 0}
    assert edited[0][1] == 5
    assert edited[0][3] == "html"
