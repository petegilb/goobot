from pydantic import BaseModel
from datetime import datetime, timezone

import asyncpg
from src.util import update_fields

CREATE_STAT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stats (
    id                SMALLINT PRIMARY KEY DEFAULT 1,  -- single-row; id=1
    last_winner_id    BIGINT REFERENCES users(id),
    last_winner_at    TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    biggest_loser_id    BIGINT REFERENCES users(id),
    biggest_loser_count    BIGINT NOT NULL DEFAULT 0
);
-- ensure the single row exists:
INSERT INTO stats (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;
"""

# ALTER TABLE stats ADD COLUMN biggest_loser_count BIGINT NOT NULL DEFAULT 0;
# UPDATE stats SET biggest_loser_count = 16 WHERE id = 1;

class Stat(BaseModel):
    """
    Global stats for the goobot
    """
    id: int
    last_winner_id: int|None
    last_winner_at: datetime|None
    created_at: datetime
    updated_at: datetime
    biggest_loser_id: int|None
    biggest_loser_count: int

async def get_stats(pool: asyncpg.Pool) -> Stat|None:
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM stats")
            return Stat.model_validate(dict(row)) if row else None

async def set_goo_lord(pool: asyncpg.Pool, last_winner_id: int, win_time: datetime):
    updates = {
        'last_winner_id': last_winner_id,
        'last_winner_at': win_time,
        'updated_at': win_time
    }
    await update_fields(pool, 'stats', 1, updates)


async def set_biggest_loser(pool: asyncpg.Pool, biggest_loser_id: int, biggest_loser_count: int):
    updates = {
        'biggest_loser_id': biggest_loser_id,
        'biggest_loser_count': biggest_loser_count,
        'updated_at': datetime.now(tz=timezone.utc)
    }
    await update_fields(pool, 'stats', 1, updates)
