import logging
import re
from typing import List, Dict, Any

import asyncpg

ALL_TABLES: List[str] = ['stats', 'users']

logger = logging.getLogger('discord')

SQL_IDENTIFIER_PATTERN = re.compile(r'^[a-z_][a-z0-9_]*$')


def check_sql_identifier(name: str):
    """
    Table and column names cannot be passed as query parameters, so they are
    interpolated into the SQL. Reject anything that is not a plain identifier.
    """
    if not SQL_IDENTIFIER_PATTERN.match(name):
        raise ValueError(f"unsafe sql identifier: {name}")


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
    if not updates:
        return

    check_sql_identifier(table)
    set_clauses = []
    params = []
    placeholder_index = 2
    for key in updates.keys():
        check_sql_identifier(key)
        set_clauses.append(f"{key} = ${placeholder_index}")
        params.append(updates[key])
        placeholder_index += 1
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
    check_sql_identifier(table)
    check_sql_identifier(field)
    sql = f"""
    UPDATE {table}
        SET {field} = {field} + $2
        WHERE id = $1
    RETURNING *;
    """
    async with pool.acquire() as connection:
        conn : asyncpg.connection.Connection = connection
        async with conn.transaction():
            await conn.execute(sql, id, delta)
