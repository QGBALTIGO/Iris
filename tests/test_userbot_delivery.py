from types import SimpleNamespace

import pytest

import app.delivery as delivery_module
from app.delivery import DeliveryManager
from app.models import MediaResource, ResourceType


class FakeBot:
    def __init__(self):
        self.copies = []

    async def get_me(self):
        return SimpleNamespace(username="IrisExampleBot")

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)
        return SimpleNamespace(message_id=777)


class FakeMessage:
    def __init__(self):
        self.chat_id = 1852596083
        self.bot = FakeBot()

    def get_bot(self):
        return self.bot


@pytest.mark.asyncio
async def test_userbot_remote_delivery_relays_through_bot_chat(monkeypatch):
    calls = {}

    async def authorized():
        return True

    async def send_to_bot(username, file_or_url, **kwargs):
        calls["send"] = (username, file_or_url, kwargs)
        return SimpleNamespace(id=321)

    async def user_id():
        return 987654321

    async def delete_from_bot_chat(username, message_id):
        calls["delete"] = (username, message_id)

    monkeypatch.setattr(delivery_module.userbot, "is_authorized", authorized)
    monkeypatch.setattr(delivery_module.userbot, "send_to_bot", send_to_bot)
    monkeypatch.setattr(delivery_module.userbot, "user_id", user_id)
    monkeypatch.setattr(delivery_module.userbot, "delete_from_bot_chat", delete_from_bot_chat)

    message = FakeMessage()
    resource = MediaResource(
        url="https://cdn.example/video.mp4",
        type=ResourceType.VIDEO,
    )
    ok = await DeliveryManager().send_remote_resource(message, resource, as_video=True, caption="Teste")

    assert ok is True
    assert calls["send"][0] == "IrisExampleBot"
    assert message.bot.copies == [{
        "chat_id": 1852596083,
        "from_chat_id": 987654321,
        "message_id": 321,
        "caption": "Teste",
    }]
    assert calls["delete"] == ("IrisExampleBot", 321)
