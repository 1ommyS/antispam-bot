from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, MessageOriginChannel

from antispam_bot.config import Settings
from antispam_bot.database import BlocklistRepository
from antispam_bot.handlers import TOPOR_RESPONSE, BotHandlers


def make_handlers(tmp_path: Path) -> BotHandlers:
    settings = Settings(
        bot_token="test-token",
        database_path=tmp_path / "bot.sqlite3",
        initial_blocked_channel_ids=frozenset(),
        superadmin_user_ids=frozenset(),
        response_text="warning",
    )
    return BotHandlers(settings, BlocklistRepository(settings.database_path))


@pytest.mark.asyncio
async def test_media_group_warning_is_claimed_only_once(tmp_path: Path) -> None:
    handlers = make_handlers(tmp_path)
    first_photo = SimpleNamespace(chat_id=-2001, media_group_id="album-1")
    second_photo = SimpleNamespace(chat_id=-2001, media_group_id="album-1")
    another_album = SimpleNamespace(chat_id=-2001, media_group_id="album-2")

    assert await handlers._claim_warning(first_photo)
    assert not await handlers._claim_warning(second_photo)
    assert await handlers._claim_warning(another_album)


@pytest.mark.asyncio
async def test_messages_outside_media_group_always_get_warning(tmp_path: Path) -> None:
    handlers = make_handlers(tmp_path)
    message = SimpleNamespace(chat_id=-2001, media_group_id=None)

    assert await handlers._claim_warning(message)
    assert await handlers._claim_warning(message)


@pytest.mark.asyncio
async def test_album_items_are_all_deleted_but_warning_is_sent_once(
    tmp_path: Path,
) -> None:
    settings = Settings(
        bot_token="test-token",
        database_path=tmp_path / "bot.sqlite3",
        initial_blocked_channel_ids=frozenset(),
        superadmin_user_ids=frozenset(),
        response_text="warning",
    )
    repository = BlocklistRepository(settings.database_path)
    await repository.connect()
    await repository.add(-2001, -1001, "Blocked channel", 7)
    handlers = BotHandlers(settings, repository)

    origin = MessageOriginChannel(
        date=datetime.now(UTC),
        chat=Chat(-1001, Chat.CHANNEL, title="Blocked channel"),
        message_id=10,
    )
    messages = [
        SimpleNamespace(
            chat_id=-2001,
            message_id=message_id,
            media_group_id="album-1",
            forward_origin=origin,
            from_user=SimpleNamespace(id=42),
            text=None,
            caption=None,
            reply_text=AsyncMock(),
            delete=AsyncMock(),
        )
        for message_id in (20, 21, 22)
    ]
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)

    for message in messages:
        await handlers.handle_message(
            SimpleNamespace(effective_message=message),
            context,
        )

    for message in messages:
        message.delete.assert_awaited_once_with()
    bot.send_message.assert_awaited_once_with(chat_id=-2001, text="warning")
    await repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "caption"),
    [
        ("топор", None),
        ("Где ТОПОР?", None),
        (None, "На фотографии — топор!"),
    ],
)
async def test_topor_in_text_or_caption_gets_reply(
    tmp_path: Path,
    text: str | None,
    caption: str | None,
) -> None:
    handlers = make_handlers(tmp_path)
    message = SimpleNamespace(
        text=text,
        caption=caption,
        forward_origin=None,
        reply_text=AsyncMock(),
        delete=AsyncMock(),
    )

    await handlers.handle_message(
        SimpleNamespace(effective_message=message),
        SimpleNamespace(),
    )

    message.reply_text.assert_awaited_once_with(TOPOR_RESPONSE)
    message.delete.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["топорик", "мотопор", "обычный текст"])
async def test_topor_must_be_a_separate_word(tmp_path: Path, text: str) -> None:
    handlers = make_handlers(tmp_path)
    message = SimpleNamespace(
        text=text,
        caption=None,
        forward_origin=None,
        reply_text=AsyncMock(),
        delete=AsyncMock(),
    )

    await handlers.handle_message(
        SimpleNamespace(effective_message=message),
        SimpleNamespace(),
    )

    message.reply_text.assert_not_awaited()
    message.delete.assert_not_awaited()
