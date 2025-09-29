import os

import discord
from discord.ext import commands
from dotenv import load_dotenv
import logging
import logging.handlers

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

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
cogs: list[str] = ["cogs.goo.goo", "cogs.greet.greet"]

print("Hello from goobot!")
intents = discord.Intents.all()
intents.message_content = True
client = commands.Bot(command_prefix="!", help_command=None, intents=intents)

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online, activity=discord.CustomActivity(name="whatever. go my gooball"))
    for cog in cogs:
        try:
            logger.info(f"Loading cog {cog}")
            await client.load_extension(cog)
        except Exception as e:
            exc = "{}: {}".format(type(e).__name__, e)
            logger.error("Failed to load cog {}\n{}".format(cog, exc))
        else:
            logger.info(f"Loaded cog {cog}")
    logger.info(f"Logged on as {client.user}!")

client.run(TOKEN, log_handler=None)
