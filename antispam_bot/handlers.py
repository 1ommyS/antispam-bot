from __future__ import annotations

import asyncio
import logging
import re
from time import monotonic

from telegram import Bot, Chat, ChatMember, Message, MessageOriginChannel, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from antispam_bot.config import Settings
from antispam_bot.database import BlockedChannel, BlocklistRepository, ChatUser

logger = logging.getLogger(__name__)

TOPOR_PATTERN = re.compile(r"(?<!\w)топор(?!\w)", re.IGNORECASE)
TOPOR_RESPONSE = "ХА! ЕГОР ЛОХ, НЕ ПРОЙДЕШЬ!"


class BotHandlers:
    def __init__(self, settings: Settings, blocklist: BlocklistRepository) -> None:
        self._settings = settings
        self._blocklist = blocklist
        self._warned_media_groups: dict[tuple[int, str], float] = {}
        self._media_group_lock = asyncio.Lock()

    async def track_users(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        users = list(message.new_chat_members)
        if message.from_user is not None:
            users.append(message.from_user)
        for user in users:
            await self._remember_user(chat.id, user)

        if message.left_chat_member is not None:
            await self._blocklist.forget_user(
                chat.id,
                message.left_chat_member.id,
            )

    async def show_my_id(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.effective_message
        user = update.effective_user
        if message is None:
            return

        if user is None:
            await message.reply_text(
                "Не удалось определить ваш ID. Отключите отправку от имени чата."
            )
            return

        await message.reply_text(f"Ваш Telegram ID: {user.id}")

    async def show_ids(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None:
            return

        await message.reply_text(
            f"ID чата: {chat.id}\n"
            f"Ваш ID: {user.id if user is not None else 'недоступен'}"
        )

    async def show_users(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._require_admin(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        try:
            administrators = await context.bot.get_chat_administrators(chat.id)
            for member in administrators:
                await self._remember_user(chat.id, member.user)
        except TelegramError:
            logger.exception("Failed to fetch administrators for chat %s", chat.id)

        users = await self._blocklist.list_users(chat.id)
        if not users:
            await message.reply_text("Бот пока не видел участников этого чата.")
            return

        lines = ["Известные боту участники:"]
        lines.extend(self._format_user(user) for user in users)
        lines.append(
            "\nTelegram не отдаёт ботам полный список участников: здесь есть "
            "администраторы и пользователи, замеченные ботом."
        )
        await self._reply_in_chunks(message, lines)

    async def block_user(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._require_admin(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        user_id = self._resolve_user_id(message, context)
        if user_id is None:
            await message.reply_text(
                "Использование: /banuser <ID пользователя> или ответьте "
                "командой на сообщение пользователя."
            )
            return

        if (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
        ):
            await self._remember_user(chat.id, message.reply_to_message.from_user)

        actor = update.effective_user
        was_added = await self._blocklist.add_blocked_user(
            chat.id,
            user_id,
            actor.id if actor is not None else None,
        )
        text = (
            f"Пользователь {user_id} добавлен в список: его пересылки из "
            "заблокированных каналов будут удаляться."
            if was_added
            else f"Пользователь {user_id} уже находится в списке."
        )
        await message.reply_text(text)

    async def unblock_user(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._require_admin(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        user_id = self._resolve_user_id(message, context)
        if user_id is None:
            await message.reply_text(
                "Использование: /unbanuser <ID пользователя> или ответьте "
                "командой на сообщение пользователя."
            )
            return

        was_removed = await self._blocklist.remove_blocked_user(chat.id, user_id)
        text = (
            f"Пользователь {user_id} удалён из списка."
            if was_removed
            else f"Пользователя {user_id} нет в списке."
        )
        await message.reply_text(text)

    async def show_blocked_users(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._require_admin(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        users = await self._blocklist.list_blocked_users(chat.id)
        if not users:
            await message.reply_text("Список заблокированных пользователей пуст.")
            return
        await self._reply_in_chunks(
            message,
            ["Заблокированные пользователи:"]
            + [self._format_user(user) for user in users],
        )

    async def block_channel(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._require_admin(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        channel = await self._resolve_channel(message, context)
        if channel is None:
            await message.reply_text(
                "Использование: /block <ID канала> или ответьте командой "
                "на пересланное сообщение из канала."
            )
            return

        user = update.effective_user
        was_added = await self._blocklist.add(
            chat.id,
            channel.channel_id,
            channel.title,
            user.id if user is not None else None,
        )
        channel_label = self._format_channel(channel)
        text = (
            f"Канал {channel_label} добавлен в блок-лист."
            if was_added
            else f"Канал {channel_label} уже заблокирован."
        )
        await message.reply_text(text)

    async def unblock_channel(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._require_admin(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        channel = await self._resolve_channel(message, context)
        if channel is None:
            await message.reply_text(
                "Использование: /unblock <ID канала> или ответьте командой "
                "на пересланное сообщение из канала."
            )
            return

        was_removed = await self._blocklist.remove(chat.id, channel.channel_id)
        channel_label = self._format_channel(channel)
        text = (
            f"Канал {channel_label} удалён из блок-листа."
            if was_removed
            else f"Канала {channel_label} нет в блок-листе."
        )
        await message.reply_text(text)

    async def show_blocklist(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._require_admin(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        channels = await self._blocklist.list_for_chat(chat.id)
        if not channels:
            await message.reply_text("Блок-лист пуст.")
            return

        channels = await self._fill_missing_titles(channels, context.bot)
        await message.reply_text(
            "Заблокированные каналы:\n"
            + "\n".join(
                f"• {self._format_channel(channel)}" for channel in channels
            )
        )

    async def refresh_channel_titles(self, bot: Bot) -> None:
        channel_ids = await self._blocklist.list_unique_channel_ids()
        for channel_id in channel_ids:
            title = await self._fetch_channel_title(bot, channel_id)
            if title is not None:
                await self._blocklist.update_title(channel_id, title)

    async def handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if message is None:
            return

        await self._reply_to_topor(message)

        forward_origin = message.forward_origin
        if forward_origin is None:
            return

        origin_chat = getattr(forward_origin, "chat", None) or getattr(
            forward_origin,
            "sender_chat",
            None,
        )
        origin_user = getattr(forward_origin, "sender_user", None)
        logger.info(
            "Forwarded message IDs: message_id=%s, chat_id=%s, "
            "source_message_id=%s, source_chat_id=%s, source_user_id=%s",
            message.message_id,
            message.chat_id,
            getattr(forward_origin, "message_id", None),
            getattr(origin_chat, "id", None),
            getattr(origin_user, "id", None),
        )

        if not isinstance(forward_origin, MessageOriginChannel):
            return

        channel_id = forward_origin.chat.id
        if not await self._blocklist.is_blocked(message.chat_id, channel_id):
            return
        sender = message.from_user
        if not await self._blocklist.should_moderate_user(
            message.chat_id,
            sender.id if sender is not None else None,
        ):
            return

        if forward_origin.chat.title:
            await self._blocklist.update_title(
                channel_id,
                forward_origin.chat.title,
            )

        logger.info(
            "Deleting message %s forwarded from channel %s in chat %s",
            message.message_id,
            channel_id,
            message.chat_id,
        )
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Failed to delete forwarded message")
            return

        if not await self._claim_warning(message):
            return
        try:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=self._settings.response_text,
            )
        except TelegramError:
            await self._release_warning(message)
            logger.exception("Failed to send spam warning")

    async def _require_admin(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> bool:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None:
            return False

        if user is not None and user.id in self._settings.superadmin_user_ids:
            return True

        if message.sender_chat is not None and message.sender_chat.id == chat.id:
            return True

        if user is not None and chat.type != Chat.PRIVATE:
            try:
                member = await context.bot.get_chat_member(chat.id, user.id)
                if member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                    return True
            except TelegramError:
                logger.exception(
                    "Failed to check admin status for user %s in chat %s",
                    user.id,
                    chat.id,
                )

        await message.reply_text("Эта команда доступна только администраторам.")
        return False

    @staticmethod
    async def _resolve_channel(
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> BlockedChannel | None:
        if context.args:
            value = context.args[0]
            try:
                channel_id = int(value)
            except ValueError:
                if not value.startswith("@"):
                    return None
                try:
                    source_chat = await context.bot.get_chat(value)
                except TelegramError:
                    return None
                if source_chat.type != Chat.CHANNEL:
                    return None
                return BlockedChannel(source_chat.id, source_chat.title)
            title = await BotHandlers._fetch_channel_title(context.bot, channel_id)
            return BlockedChannel(channel_id, title)

        replied_message = message.reply_to_message
        if replied_message is None:
            return None
        origin = replied_message.forward_origin
        if isinstance(origin, MessageOriginChannel):
            return BlockedChannel(origin.chat.id, origin.chat.title)
        return None

    async def _fill_missing_titles(
        self,
        channels: list[BlockedChannel],
        bot: Bot,
    ) -> list[BlockedChannel]:
        result: list[BlockedChannel] = []
        for channel in channels:
            if channel.title is not None:
                result.append(channel)
                continue

            title = await self._fetch_channel_title(bot, channel.channel_id)
            if title is not None:
                await self._blocklist.update_title(channel.channel_id, title)
            result.append(BlockedChannel(channel.channel_id, title))
        return result

    @staticmethod
    async def _fetch_channel_title(bot: Bot, channel_id: int) -> str | None:
        try:
            source_chat = await bot.get_chat(channel_id)
        except TelegramError as error:
            logger.warning(
                "Could not resolve title for channel %s: %s",
                channel_id,
                error,
            )
            return None
        if source_chat.type != Chat.CHANNEL:
            return None
        return source_chat.title

    @staticmethod
    def _format_channel(channel: BlockedChannel) -> str:
        if channel.title is None:
            return f"{channel.channel_id} (название недоступно)"
        return f"{channel.title} — {channel.channel_id}"

    async def _remember_user(self, chat_id: int, user: User) -> None:
        await self._blocklist.remember_user(
            chat_id,
            user.id,
            user.username,
            user.full_name,
        )

    @staticmethod
    async def _reply_to_topor(message: Message) -> None:
        content = message.text or message.caption
        if content is None or TOPOR_PATTERN.search(content) is None:
            return
        try:
            await message.reply_text(TOPOR_RESPONSE)
        except TelegramError:
            logger.exception("Failed to reply to message containing 'топор'")

    @staticmethod
    def _resolve_user_id(
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int | None:
        if context.args:
            try:
                return int(context.args[0])
            except ValueError:
                return None
        if message.reply_to_message is None:
            return None
        replied_user = message.reply_to_message.from_user
        return replied_user.id if replied_user is not None else None

    @staticmethod
    def _format_user(user: ChatUser) -> str:
        label = user.full_name or "имя неизвестно"
        if user.username:
            label += f" (@{user.username})"
        return f"• {label} — {user.user_id}"

    @staticmethod
    async def _reply_in_chunks(message: Message, lines: list[str]) -> None:
        chunk = ""
        for line in lines:
            candidate = f"{chunk}\n{line}" if chunk else line
            if len(candidate) <= 4000:
                chunk = candidate
                continue
            await message.reply_text(chunk)
            chunk = line
        if chunk:
            await message.reply_text(chunk)

    async def _claim_warning(self, message: Message) -> bool:
        media_group_id = message.media_group_id
        if media_group_id is None:
            return True

        key = (message.chat_id, media_group_id)
        now = monotonic()
        async with self._media_group_lock:
            cutoff = now - 300
            self._warned_media_groups = {
                group_key: timestamp
                for group_key, timestamp in self._warned_media_groups.items()
                if timestamp >= cutoff
            }
            if key in self._warned_media_groups:
                return False
            self._warned_media_groups[key] = now
            return True

    async def _release_warning(self, message: Message) -> None:
        if message.media_group_id is None:
            return
        async with self._media_group_lock:
            self._warned_media_groups.pop(
                (message.chat_id, message.media_group_id),
                None,
            )
