import pytest

from claude_bridge.bus import InboundMessage, MessageBus, OutboundMessage


def test_inbound_message_session_key():
    msg = InboundMessage(channel="telegram", chat_id="123", sender_id="456", text="hello")
    assert msg.session_key == "telegram:123"


def test_outbound_message_defaults():
    msg = OutboundMessage(channel="telegram", chat_id="123", text="hi")
    assert msg.kind == "new"
    assert msg.message_id is None
    assert msg.media is None


def test_outbound_message_edit():
    msg = OutboundMessage(
        channel="telegram", chat_id="123", text="updated", kind="edit", message_id=42
    )
    assert msg.kind == "edit"
    assert msg.message_id == 42


@pytest.mark.asyncio
async def test_message_bus_inbound_roundtrip():
    bus = MessageBus()
    msg = InboundMessage(channel="telegram", chat_id="1", sender_id="2", text="hi")
    await bus.publish_inbound(msg)
    received = await bus.consume_inbound()
    assert received.text == "hi"
    assert bus.inbound.qsize() == 0


@pytest.mark.asyncio
async def test_message_bus_outbound_roundtrip():
    bus = MessageBus()
    msg = OutboundMessage(channel="feishu", chat_id="ou_1", text="reply")
    await bus.publish_outbound(msg)
    received = await bus.consume_outbound()
    assert received.channel == "feishu"
