from pydantic import BaseModel
from datetime import datetime
from typing import Optional, TYPE_CHECKING, Dict, Any

import asyncpg
import discord
from discord.ext import commands
from src.util import update_fields, increment_field

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

CREATE_USER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hopped_goo BIGINT NOT NULL DEFAULT 0,
    win_count BIGINT NOT NULL DEFAULT 0,
    loss_count BIGINT NOT NULL DEFAULT 0,
    lord_time BIGINT NOT NULL DEFAULT 0,
    last_win TIMESTAMPTZ
);
"""

class User(BaseModel):
    id: int
    username: str
    created_at: datetime
    hopped_goo: int
    win_count: int
    loss_count: int
    lord_time: int
    last_win: datetime|None

async def init_user(pool: asyncpg.Pool, ctx: commands.Context, member: discord.Member = None) -> User:
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
            row = await conn.fetchrow(sql, ctx.author.id, ctx.author.name)
            return User.model_validate(dict(row)) if row else None

async def get_user(pool: asyncpg.Pool, id:int):
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM users WHERE id=$1;", id)
            return User.model_validate(dict(row)) if row else None

async def update_user(pool: asyncpg.Pool, id:int, updates: Dict[str, Any]):
    await update_fields(pool, 'users', id, updates)

async def increment_user_field(pool: asyncpg.Pool, id:int, field: str, delta:int=1):
    await increment_field(pool, 'users', id, field, delta)
