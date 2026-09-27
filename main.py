import logging
import os

from telegram import MessageOriginChannel, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]

# ID каналов, пересланные сообщения из которых нужно удалять.
# Например: -1001234567890,-1009876543210
BLOCKED_CHANNEL_IDS = {
    int(chat_id)
    for chat_id in os.environ["BLOCKED_CHANNEL_IDS"].split(",")
    if chat_id.strip()
}

RESPONSE_TEXT = "ЕГОР НЕ СПАМЬ ХУЕТОЙ"


async def handle_chat_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat

    if chat is None:
        return

    await context.bot.send_message(
        chat_id=chat.id,
        text=f"ID этого чата: {chat.id}",
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message is None:
        return

    # Канал, из которого сообщение было переслано
    forward_origin = message.forward_origin

    if not isinstance(forward_origin, MessageOriginChannel):
        return

    origin_channel_id = forward_origin.chat.id

    if origin_channel_id not in BLOCKED_CHANNEL_IDS:
        return

    logger.info(
        "Deleting message %s forwarded from channel %s",
        message.message_id,
        origin_channel_id,
    )

    try:
        await message.delete()

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=RESPONSE_TEXT,
        )

    except Exception:
        logger.exception("Failed to delete message")


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("chatid", handle_chat_id),
    )

    application.add_handler(
        MessageHandler(
            filters.ALL,
            handle_message,
        )
    )

    logger.info("Bot started")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()