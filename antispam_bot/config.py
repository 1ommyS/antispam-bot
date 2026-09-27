from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_ids(value: str, variable_name: str) -> frozenset[int]:
    try:
        return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise ValueError(
            f"{variable_name} must contain comma-separated integer IDs"
        ) from error


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    database_path: Path
    initial_blocked_channel_ids: frozenset[int]
    superadmin_user_ids: frozenset[int]
    response_text: str

    @classmethod
    def from_env(cls) -> Settings:
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise ValueError("BOT_TOKEN is required")

        return cls(
            bot_token=bot_token,
            database_path=Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")),
            initial_blocked_channel_ids=_parse_ids(
                os.getenv("BLOCKED_CHANNEL_IDS", ""),
                "BLOCKED_CHANNEL_IDS",
            ),
            superadmin_user_ids=_parse_ids(
                os.getenv("ADMIN_USER_IDS", ""),
                "ADMIN_USER_IDS",
            ),
            response_text=os.getenv("RESPONSE_TEXT", "ЕГОР НЕ СПАМЬ ХУЙНЕЙ"),
        )
