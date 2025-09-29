from datetime import datetime
from typing import List

import asyncpg
import discord
from discord.ext import commands
from src.models.user import CREATE_USER_TABLE_SQL

ALL_TABLES: List[str] = ['users']

async def init_tables(pool: asyncpg.Pool):
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        await conn.execute(CREATE_USER_TABLE_SQL)
