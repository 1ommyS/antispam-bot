import sqlite3
from pathlib import Path

import pytest

from antispam_bot.database import BlockedChannel, BlocklistRepository, ChatUser


@pytest.mark.asyncio
async def test_blocklist_is_scoped_by_chat_and_persists(tmp_path: Path) -> None:
    database_path = tmp_path / "bot.sqlite3"
    repository = BlocklistRepository(database_path, {-1001})
    await repository.connect()

    assert await repository.is_blocked(-2001, -1001)
    assert await repository.add(-2001, -1002, "Test channel", 42)
    assert not await repository.is_blocked(-2002, -1002)
    assert await repository.remove(-2001, -1001)
    await repository.close()

    reopened = BlocklistRepository(database_path, {-1001})
    await reopened.connect()
    assert not await reopened.is_blocked(-2001, -1001)
    assert await reopened.list_for_chat(-2001) == [
        BlockedChannel(-1002, "Test channel")
    ]
    await reopened.close()


@pytest.mark.asyncio
async def test_add_and_remove_report_existing_state(tmp_path: Path) -> None:
    repository = BlocklistRepository(tmp_path / "bot.sqlite3")
    await repository.connect()

    assert await repository.add(-2001, -1001, None, 42)
    assert not await repository.add(-2001, -1001, "Updated title", 42)
    assert await repository.list_for_chat(-2001) == [
        BlockedChannel(-1001, "Updated title")
    ]
    assert await repository.remove(-2001, -1001)
    assert not await repository.remove(-2001, -1001)

    await repository.close()


@pytest.mark.asyncio
async def test_existing_database_gets_channel_title_column(tmp_path: Path) -> None:
    database_path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE blocked_channels (
            chat_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            added_by_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, channel_id)
        )
        """
    )
    connection.commit()
    connection.close()

    repository = BlocklistRepository(database_path)
    await repository.connect()
    assert await repository.add(-2001, -1001, "Migrated channel", 42)
    assert await repository.list_for_chat(-2001) == [
        BlockedChannel(-1001, "Migrated channel")
    ]
    await repository.close()


@pytest.mark.asyncio
async def test_chat_users_are_remembered_updated_and_forgotten(tmp_path: Path) -> None:
    repository = BlocklistRepository(tmp_path / "bot.sqlite3")
    await repository.connect()

    await repository.remember_user(-2001, 42, "old_name", "Old Name")
    await repository.remember_user(-2001, 42, "new_name", "New Name")
    await repository.remember_user(-2001, 7, None, "Another User")
    await repository.remember_user(-2002, 99, "other_chat", "Other Chat")

    assert await repository.list_users(-2001) == [
        ChatUser(7, None, "Another User"),
        ChatUser(42, "new_name", "New Name"),
    ]

    await repository.forget_user(-2001, 7)
    assert await repository.list_users(-2001) == [
        ChatUser(42, "new_name", "New Name")
    ]
    await repository.close()


@pytest.mark.asyncio
async def test_targeted_user_filter_preserves_legacy_mode_until_enabled(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "bot.sqlite3"
    repository = BlocklistRepository(database_path)
    await repository.connect()

    assert await repository.should_moderate_user(-2001, 42)
    assert await repository.should_moderate_user(-2001, None)

    assert await repository.add_blocked_user(-2001, 42, 7)
    assert not await repository.add_blocked_user(-2001, 42, 7)
    assert await repository.should_moderate_user(-2001, 42)
    assert not await repository.should_moderate_user(-2001, 99)
    assert not await repository.should_moderate_user(-2001, None)
    assert await repository.list_blocked_users(-2001) == [ChatUser(42, None, "")]

    assert await repository.remove_blocked_user(-2001, 42)
    assert not await repository.should_moderate_user(-2001, 42)
    await repository.close()

    reopened = BlocklistRepository(database_path)
    await reopened.connect()
    assert not await reopened.should_moderate_user(-2001, 42)
    await reopened.close()
