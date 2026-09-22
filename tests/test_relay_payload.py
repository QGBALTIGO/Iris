from app.delivery import parse_relay_caption, parse_relay_payload, relay_caption


def test_relay_payload_keeps_legacy_parser_compatible():
    value = relay_caption(
        1852596083,
        "🎬 Teste",
        queue_item_id=42,
        expected_kind="video",
    )
    assert parse_relay_caption(value) == (1852596083, "🎬 Teste")
    payload = parse_relay_payload(value)
    assert payload == {
        "chat_id": 1852596083,
        "caption": "🎬 Teste",
        "queue_item_id": 42,
        "expected_kind": "video",
    }
