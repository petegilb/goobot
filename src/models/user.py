from pydantic import BaseModel
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import asyncpg
import discord
from discord.ext import commands

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

CREATE_USER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

class User(BaseModel):
    id: int
    username: str
    created_at: datetime

async def init_user(pool: asyncpg.Pool, ctx: commands.Context, member: discord.Member = None) -> User:
    sql = """
    INSERT INTO users (id, username)
    VALUES ($1, $2)
    ON CONFLICT (id) DO UPDATE
      SET username = EXCLUDED.username
    RETURNING id, username, created_at
    """
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        row = await conn.fetchrow(sql, ctx.author.id, ctx.author.name)
        return User.model_validate(dict(row)) if row else None
