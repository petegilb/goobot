import logging
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord.ext import commands
from src.util import init_tables, ALL_TABLES

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

logger = logging.getLogger('discord')

class Admin(commands.Cog):

    def __init__(self, bot):
        self.bot: GooBot = bot

    @commands.command()
    @commands.has_permissions(administrator = True)
    async def clear_db(self, ctx: commands.Context):
        logger.warning("Dropping all db tables!")
        pool = self.bot.db_pool
        async with pool.acquire() as connection:
            conn : asyncpg.connection.Connection = connection
            for table in ALL_TABLES:
                await conn.execute(f"DROP TABLE {table};")
        await init_tables(pool)
        await ctx.send("DB RESET!")

    @commands.hybrid_command(
        name="ping",
        description="Check if the bot is alive.",
    )
    async def ping(self, context: commands.Context) -> None:
        """
        sourced from: https://github.com/kkrypt0nn/Python-Discord-Bot-Template/blob/main/cogs/general.py
        Check if the bot is alive.

        :param context: The hybrid command context.
        """
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"The bot latency is {round(self.bot.latency * 1000)}ms.",
            color=0xBEBEFE,
        )
        await context.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
