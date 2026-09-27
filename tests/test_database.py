import sqlite3
from pathlib import Path

import pytest

from antispam_bot.database import BlockedChannel, BlocklistRepository


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
