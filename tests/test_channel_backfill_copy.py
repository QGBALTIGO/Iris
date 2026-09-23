from types import SimpleNamespace

import pytest

from app.channel_backfill import ChannelBackfill


@pytest.mark.asyncio
async def test_copy_existing_private_message_uses_telegram_server_side():
    calls = {}

    class FakeBot:
        async def copy_message(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(message_id=9876)

    worker = ChannelBackfill.__new__(ChannelBackfill)
    result = await worker._copy_existing_private_message(
        bot=FakeBot(),
        source_chat_id=1852596083,
        source_message_id=4321,
        destination_chat_id=-1001234567890,
    )

    assert result == 9876
    assert calls == {
        "chat_id": -1001234567890,
        "from_chat_id": 1852596083,
        "message_id": 4321,
    }
