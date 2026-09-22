from types import SimpleNamespace

import pytest

import app.delivery as delivery_module
from app.delivery import DeliveryManager, parse_relay_caption
from app.models import MediaResource, ResourceType


class FakeBot:
    async def get_me(self):
        return SimpleNamespace(username="IrisExampleBot")


class FakeMessage:
    def __init__(self):
        self.chat_id = 1852596083
        self.bot = FakeBot()

    def get_bot(self):
        return self.bot


@pytest.mark.asyncio
async def test_userbot_remote_delivery_targets_bot_username_not_peeruser(monkeypatch):
    calls = {}

    async def authorized():
        return True

    async def send_to_bot(username, file_or_url, **kwargs):
        calls["send"] = (username, file_or_url, kwargs)
        return SimpleNamespace(id=321)

    monkeypatch.setattr(delivery_module.userbot, "is_authorized", authorized)
    monkeypatch.setattr(delivery_module.userbot, "send_to_bot", send_to_bot)

    message = FakeMessage()
    resource = MediaResource(
        url="https://cdn.example/video.mp4",
        type=ResourceType.VIDEO,
    )
    ok = await DeliveryManager().send_remote_resource(
        message,
        resource,
        as_video=True,
        caption="Teste",
    )

    assert ok is True
    assert calls["send"][0] == "IrisExampleBot"
    assert calls["send"][1] == "https://cdn.example/video.mp4"
    relay = parse_relay_caption(calls["send"][2]["caption"])
    assert relay == (1852596083, "Teste")
    assert calls["send"][2]["as_video"] is True


def test_relay_caption_rejects_invalid_payload():
    assert parse_relay_caption(None) is None
    assert parse_relay_caption("hello") is None
    assert parse_relay_caption("IRIS_RELAY:not-base64") is None
