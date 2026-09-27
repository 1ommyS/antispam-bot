from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from antispam_bot.config import Settings
from antispam_bot.database import BlocklistRepository
from antispam_bot.handlers import BotHandlers

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def build_application(settings: Settings) -> Application:
    blocklist = BlocklistRepository(
        settings.database_path,
        settings.initial_blocked_channel_ids,
    )
    handlers = BotHandlers(settings, blocklist)

    async def post_init(application: Application) -> None:
        await blocklist.connect()
        await handlers.refresh_channel_titles(application.bot)
        await application.bot.set_my_commands(
            (
                BotCommand("myid", "Показать ваш Telegram ID"),
                BotCommand("id", "Показать ID чата и пользователя"),
                BotCommand("users", "Показать известные ID участников"),
                BotCommand("block", "Заблокировать канал"),
                BotCommand("unblock", "Разблокировать канал"),
                BotCommand("blocklist", "Показать заблокированные каналы"),
                BotCommand("banuser", "Фильтровать пересылки пользователя"),
                BotCommand("unbanuser", "Снять фильтр с пользователя"),
                BotCommand("banlist", "Показать пользователей под фильтром"),
            )
        )

    async def post_shutdown(application: Application) -> None:
        del application
        await blocklist.close()

    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(
        MessageHandler(filters.ALL, handlers.track_users),
        group=-1,
    )
    application.add_handler(CommandHandler("myid", handlers.show_my_id))
    application.add_handler(CommandHandler(("id", "chatid"), handlers.show_ids))
    application.add_handler(CommandHandler("users", handlers.show_users))
    application.add_handler(CommandHandler("block", handlers.block_channel))
    application.add_handler(CommandHandler("unblock", handlers.unblock_channel))
    application.add_handler(CommandHandler("blocklist", handlers.show_blocklist))
    application.add_handler(CommandHandler(("banuser", "ban"), handlers.block_user))
    application.add_handler(
        CommandHandler(("unbanuser", "unban"), handlers.unblock_user)
    )
    application.add_handler(CommandHandler("banlist", handlers.show_blocked_users))
    application.add_handler(MessageHandler(filters.ALL, handlers.handle_message))
    return application


def main() -> None:
    settings = Settings.from_env()
    application = build_application(settings)
    logger.info("Bot started; database=%s", settings.database_path)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
