from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


@dataclass(frozen=True, slots=True)
class BlockedChannel:
    channel_id: int
    title: str | None


@dataclass(frozen=True, slots=True)
class ChatUser:
    user_id: int
    username: str | None
    full_name: str


class BlocklistRepository:
    def __init__(
        self,
        database_path: Path,
        initial_channel_ids: Iterable[int] = (),
    ) -> None:
        self._database_path = database_path
        self._initial_channel_ids = tuple(initial_channel_ids)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._database_path)
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS blocked_channels (
                chat_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                channel_title TEXT,
                added_by_user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, channel_id)
            )
            """
        )
        columns_cursor = await self._connection.execute(
            "PRAGMA table_info(blocked_channels)"
        )
        columns = {row[1] for row in await columns_cursor.fetchall()}
        if "channel_title" not in columns:
            await self._connection.execute(
                "ALTER TABLE blocked_channels ADD COLUMN channel_title TEXT"
            )
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS initialized_chats (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_users (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS blocked_users (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                added_by_user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_filter_chats (
                chat_id INTEGER PRIMARY KEY,
                enabled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def is_blocked(self, chat_id: int, channel_id: int) -> bool:
        async with self._lock:
            connection = self._require_connection()
            await self._initialize_chat(connection, chat_id)
            cursor = await connection.execute(
                "SELECT 1 FROM blocked_channels WHERE chat_id = ? AND channel_id = ?",
                (chat_id, channel_id),
            )
            return await cursor.fetchone() is not None

    async def add(
        self,
        chat_id: int,
        channel_id: int,
        channel_title: str | None,
        added_by: int | None,
    ) -> bool:
        async with self._lock:
            connection = self._require_connection()
            await self._initialize_chat(connection, chat_id)
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO blocked_channels (
                    chat_id, channel_id, channel_title, added_by_user_id
                ) VALUES (?, ?, ?, ?)
                """,
                (chat_id, channel_id, channel_title, added_by),
            )
            was_added = cursor.rowcount > 0
            if channel_title is not None:
                await connection.execute(
                    """
                    UPDATE blocked_channels
                    SET channel_title = ?
                    WHERE channel_id = ?
                    """,
                    (channel_title, channel_id),
                )
            await connection.commit()
            return was_added

    async def remove(self, chat_id: int, channel_id: int) -> bool:
        async with self._lock:
            connection = self._require_connection()
            await self._initialize_chat(connection, chat_id)
            cursor = await connection.execute(
                "DELETE FROM blocked_channels WHERE chat_id = ? AND channel_id = ?",
                (chat_id, channel_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def list_for_chat(self, chat_id: int) -> list[BlockedChannel]:
        async with self._lock:
            connection = self._require_connection()
            await self._initialize_chat(connection, chat_id)
            cursor = await connection.execute(
                """
                SELECT channel_id, channel_title FROM blocked_channels
                WHERE chat_id = ? ORDER BY channel_id
                """,
                (chat_id,),
            )
            rows = await cursor.fetchall()
            return [BlockedChannel(channel_id=row[0], title=row[1]) for row in rows]

    async def list_unique_channel_ids(self) -> list[int]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "SELECT DISTINCT channel_id FROM blocked_channels ORDER BY channel_id"
            )
            return [row[0] for row in await cursor.fetchall()]

    async def update_title(self, channel_id: int, channel_title: str) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                UPDATE blocked_channels
                SET channel_title = ?
                WHERE channel_id = ?
                """,
                (channel_title, channel_id),
            )
            await connection.commit()

    async def remember_user(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        full_name: str,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT INTO chat_users (chat_id, user_id, username, full_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (chat_id, user_id, username, full_name),
            )
            await connection.commit()

    async def forget_user(self, chat_id: int, user_id: int) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                "DELETE FROM chat_users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            await connection.commit()

    async def list_users(self, chat_id: int) -> list[ChatUser]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT user_id, username, full_name
                FROM chat_users
                WHERE chat_id = ?
                ORDER BY full_name COLLATE NOCASE, user_id
                """,
                (chat_id,),
            )
            return [ChatUser(*row) for row in await cursor.fetchall()]

    async def add_blocked_user(
        self,
        chat_id: int,
        user_id: int,
        added_by: int | None,
    ) -> bool:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                "INSERT OR IGNORE INTO user_filter_chats (chat_id) VALUES (?)",
                (chat_id,),
            )
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO blocked_users (
                    chat_id, user_id, added_by_user_id
                ) VALUES (?, ?, ?)
                """,
                (chat_id, user_id, added_by),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def remove_blocked_user(self, chat_id: int, user_id: int) -> bool:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "DELETE FROM blocked_users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def list_blocked_users(self, chat_id: int) -> list[ChatUser]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT blocked_users.user_id, chat_users.username,
                       COALESCE(chat_users.full_name, '')
                FROM blocked_users
                LEFT JOIN chat_users USING (chat_id, user_id)
                WHERE blocked_users.chat_id = ?
                ORDER BY blocked_users.user_id
                """,
                (chat_id,),
            )
            return [ChatUser(*row) for row in await cursor.fetchall()]

    async def is_user_blocked(self, chat_id: int, user_id: int) -> bool:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT 1 FROM blocked_users
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            )
            return await cursor.fetchone() is not None

    async def should_moderate_user(
        self,
        chat_id: int,
        user_id: int | None,
    ) -> bool:
        """Keep legacy all-user filtering until targeted mode is activated."""
        async with self._lock:
            connection = self._require_connection()
            mode_cursor = await connection.execute(
                "SELECT 1 FROM user_filter_chats WHERE chat_id = ?",
                (chat_id,),
            )
            if await mode_cursor.fetchone() is None:
                return True
            if user_id is None:
                return False
            user_cursor = await connection.execute(
                """
                SELECT 1 FROM blocked_users
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            )
            return await user_cursor.fetchone() is not None

    async def _initialize_chat(
        self,
        connection: aiosqlite.Connection,
        chat_id: int,
    ) -> None:
        cursor = await connection.execute(
            "SELECT 1 FROM initialized_chats WHERE chat_id = ?",
            (chat_id,),
        )
        if await cursor.fetchone() is not None:
            return

        await connection.executemany(
            """
            INSERT OR IGNORE INTO blocked_channels (
                chat_id, channel_id, channel_title, added_by_user_id
            ) VALUES (?, ?, NULL, NULL)
            """,
            ((chat_id, channel_id) for channel_id in self._initial_channel_ids),
        )
        await connection.execute(
            "INSERT INTO initialized_chats (chat_id) VALUES (?)",
            (chat_id,),
        )
        await connection.commit()

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database connection is not initialized")
        return self._connection
