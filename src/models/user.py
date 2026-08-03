from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Any, List

import asyncpg
import discord
from src.util import update_fields, increment_field

CREATE_USER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hopped_goo BIGINT NOT NULL DEFAULT 0,
    win_count BIGINT NOT NULL DEFAULT 0,
    loss_count BIGINT NOT NULL DEFAULT 0,
    lord_time BIGINT NOT NULL DEFAULT 0,
    last_win TIMESTAMPTZ,
    loss_streak BIGINT NOT NULL DEFAULT 0
);
"""

# ALTER TABLE users ADD COLUMN loss_streak BIGINT NOT NULL DEFAULT 0;

class User(BaseModel):
    id: int
    username: str
    created_at: datetime
    hopped_goo: int
    win_count: int
    loss_count: int
    lord_time: int
    last_win: datetime|None
    loss_streak: int

async def init_user(pool: asyncpg.Pool, member: discord.Member) -> User|None:
    sql = """
    INSERT INTO users (id, username)
    VALUES ($1, $2)
    ON CONFLICT (id) DO UPDATE
      SET username = EXCLUDED.username
    RETURNING *;
    """
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            row = await conn.fetchrow(sql, member.id, member.display_name)
            return User.model_validate(dict(row)) if row else None

async def get_user(pool: asyncpg.Pool, id:int) -> User|None:
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM users WHERE id=$1;", id)
            return User.model_validate(dict(row)) if row else None

async def get_all_users(pool: asyncpg.Pool) -> List[User]:
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            db_users = await conn.fetch("SELECT * FROM users;")
            return [User.model_validate(dict(row)) for row in db_users]

async def record_win(pool: asyncpg.Pool, id:int, username:str, win_time: datetime) -> User|None:
    """
    Counters are incremented in SQL so simultaneous hops cannot overwrite each other.
    """
    sql = """
    UPDATE users
        SET hopped_goo = hopped_goo + 1,
            win_count = win_count + 1,
            loss_count = 0,
            last_win = $2,
            username = $3
        WHERE id = $1
    RETURNING *;
    """
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            row = await conn.fetchrow(sql, id, win_time, username)
            return User.model_validate(dict(row)) if row else None

async def record_loss(pool: asyncpg.Pool, id:int, username:str) -> User|None:
    sql = """
    UPDATE users
        SET hopped_goo = hopped_goo + 1,
            loss_count = loss_count + 1,
            loss_streak = GREATEST(loss_streak, loss_count + 1),
            username = $2
        WHERE id = $1
    RETURNING *;
    """
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            row = await conn.fetchrow(sql, id, username)
            return User.model_validate(dict(row)) if row else None

async def update_user(pool: asyncpg.Pool, id:int, updates: Dict[str, Any]):
    await update_fields(pool, 'users', id, updates)

async def increment_user_field(pool: asyncpg.Pool, id:int, field: str, delta:int=1):
    await increment_field(pool, 'users', id, field, delta)
