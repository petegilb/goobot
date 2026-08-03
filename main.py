import os
import logging
import logging.handlers
from datetime import datetime
from typing import Dict
from dotenv import load_dotenv

import aiohttp
import discord
from discord.ext import commands
import asyncpg
from src.util import init_tables

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PG_USER = os.getenv("POSTGRES_USER", 'postgres')
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD")
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is not set, check your .env")

# referenced: https://discordpy.readthedocs.io/en/stable/logging.html#setting-up-logging
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)

logging.getLogger('discord.http').setLevel(logging.INFO)
stream_handler = logging.StreamHandler()
handler = logging.handlers.RotatingFileHandler(
    filename='discord.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate through 5 files
)
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.addHandler(stream_handler)

# referenced: https://github.com/egeyardimci/Discord-Py-Bot-Template/blob/master/bot.py
cogs: list[str] = ["cogs.goo.goo", "cogs.greet.greet", "cogs.admin.admin", "cogs.game.game", "cogs.help.help"]

print("Hello from goobot!")
intents = discord.Intents.all()
intents.message_content = True

class GooBot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.db_pool: asyncpg.Pool | None = None
        self.http_session: aiohttp.ClientSession | None = None
        self.jail_counter: Dict[int, datetime] = {}

    async def setup_hook(self):
        self.db_pool = await asyncpg.create_pool(
            user=PG_USER, password=PG_PASSWORD,
            database='postgres', host=PG_HOST
        )

        await init_tables(self.db_pool)
        self.http_session = aiohttp.ClientSession()

        # loading here rather than in on_ready, which fires again on every reconnect
        for cog in cogs:
            try:
                logger.info(f"Loading cog {cog}")
                await self.load_extension(cog)
            except Exception as e:
                exc = "{}: {}".format(type(e).__name__, e)
                logger.error("Failed to load cog {}\n{}".format(cog, exc))
            else:
                logger.info(f"Loaded cog {cog}")

        await self.sync_commands()

    async def sync_commands(self):
        try:
            if GUILD_ID:
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
        except discord.HTTPException:
            logger.exception("Failed to sync the application command tree")

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        if self.db_pool:
            await self.db_pool.close()
        await super().close()

    async def on_command_error(self, ctx: commands.Context, exception):
        if isinstance(exception, commands.CommandOnCooldown):
            await ctx.reply(
                f'{ctx.command} is on cooldown, {ctx.author.display_name}, you can use it in {round(exception.retry_after, 2)} seconds...',
                delete_after=5, silent=True
            )
            return

        # a command used outside a goo channel or a typo is not worth logging
        if isinstance(exception, (commands.CheckFailure, commands.CommandNotFound)):
            return

        if isinstance(exception, commands.BadArgument):
            await ctx.reply("I couldn't find that user.", delete_after=10)
            return

        return await super().on_command_error(ctx, exception)

client = GooBot(command_prefix="!", help_command=None, intents=intents)

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online, activity=discord.CustomActivity(name="whatever. go my gooball"))
    logger.info(f"Logged on as {client.user}!")

client.run(TOKEN, log_handler=None)
