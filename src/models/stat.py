from pydantic import BaseModel
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import asyncpg
import discord
from discord.ext import commands

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

CREATE_STAT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stats (
    id                SMALLINT PRIMARY KEY DEFAULT 1,  -- single-row; id=1
    last_winner_id    BIGINT REFERENCES users(id),
    last_winner_at    TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- ensure the single row exists:
INSERT INTO stats (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;
"""

class Stat(BaseModel):
    """
    Global stats for the goobot
    """
    id: int
    last_winner_id: int|None
    last_winner_at: datetime|None
    created_at: datetime
    updated_at: datetime

async def get_stats(pool: asyncpg.Pool) -> Stat:
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM stats")
            return Stat.model_validate(dict(row)) if row else None

# TODO remove this (im just using it for reference)
async def set_last_winner(pool, winner_id: int):
    async with pool.acquire() as conn:
        async with conn.transaction():
            # lock the single-row so concurrent updates serialize
            row = await conn.fetchrow("SELECT * FROM stats WHERE id=1 FOR UPDATE")
            if not row:
                # create the single row if somehow missing
                await conn.execute(
                    "INSERT INTO stats (id, last_winner_id, last_winner_at, total_games) VALUES (1, $1, now(), 1)",
                    winner_id
                )
                return
            await conn.execute(
                """
                UPDATE stats
                   SET last_winner_id = $1,
                       last_winner_at = now(),
                       total_games = stats.total_games + 1,
                       updated_at = now()
                 WHERE id = 1
                """,
                winner_id
            )
