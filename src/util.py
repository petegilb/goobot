import logging
from datetime import datetime
from typing import List, Dict, Any

import asyncpg
import discord
from discord.ext import commands

ALL_TABLES: List[str] = ['stats', 'users']

logger = logging.getLogger('discord')

async def init_tables(pool: asyncpg.Pool):
    from src.models.user import CREATE_USER_TABLE_SQL
    from src.models.stat import CREATE_STAT_TABLE_SQL
    sql_commands = [CREATE_USER_TABLE_SQL, CREATE_STAT_TABLE_SQL]
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            for command in sql_commands:
                await conn.execute(command)


async def update_fields(pool: asyncpg.Pool, table:str, id:int, updates: Dict[str, Any]):
    set_clauses = []
    params = []
    i = 2
    for key in updates.keys():
        set_clauses.append(f"{key} = ${i}")
        params.append(updates[key])
        i+=1
    set_sql = ", ".join(set_clauses)
    sql = f"""
    UPDATE {table}
        SET {set_sql}
        WHERE id = $1
    RETURNING *;
    """

    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            await conn.execute(sql, id, *params)

async def increment_field(pool: asyncpg.Pool, table:str, id:int, field: str, delta:int=1):
    sql = f"""
    UPDATE {table}
        SET {field} = {field} + $2,
        WHERE id = $1
    RETURNING *;
    """
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            await conn.execute(sql, id, delta)
