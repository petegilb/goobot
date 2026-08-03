import logging
import os
import base64
import binascii
from io import BytesIO
from typing import TYPE_CHECKING

import aiohttp
import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import GooBot

logger = logging.getLogger('discord')

MC_SERVER_IP = os.getenv("MC_SERVER_IP", "localhost")
MC_API_TIMEOUT = 10


def decode_favicon(favicon: str|None) -> bytes|None:
    if not favicon:
        return None
    encoded = favicon.replace('data:image/png;base64,', '')
    try:
        return base64.b64decode(encoded)
    except (binascii.Error, ValueError):
        logger.warning("Could not decode the server favicon")
        return None


def parse_version(server: dict|None) -> str:
    name = (server or {}).get('name') or ""
    parts = name.split(' ')
    return parts[1] if len(parts) > 1 else (name or "unknown")


class Game(commands.Cog):

    def __init__(self, bot):
        self.bot: GooBot = bot

    @commands.command()
    async def minecraft(self, ctx: commands.Context):
        session = self.bot.http_session
        if session is None:
            logger.error("no http session available")
            return

        url = f"https://mcapi.us/server/status?ip={MC_SERVER_IP}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=MC_API_TIMEOUT)) as response:
                if response.status != 200:
                    await ctx.reply(f"Couldn't reach the server at {MC_SERVER_IP}")
                    return
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            logger.exception(f"Failed to query the status of {MC_SERVER_IP}")
            await ctx.reply(f"Couldn't reach the server at {MC_SERVER_IP}")
            return

        # the api answers with 200 and null fields when the server is down
        is_online = data.get('online') is True
        if not is_online:
            await ctx.reply(f"{MC_SERVER_IP} is offline.")
            return

        players = data.get('players') or {}
        current_players = players.get('now', 0)
        max_players = players.get('max', 0)
        player_names = players.get('sample') or []
        version = parse_version(data.get('server'))

        player_names_str = "__Players__"
        for player_name in player_names:
            player_names_str += f"\n{player_name.get('name')}"
        if not player_names:
            player_names_str += "\nnobody is online"

        embed = discord.Embed(
            title=f"{MC_SERVER_IP}",
            color=0x39e81a,
        )
        embed.description = player_names_str

        # kept in memory so concurrent calls cannot overwrite each other's icon
        icon_content = decode_favicon(data.get('favicon'))
        attachments = []
        if icon_content:
            attachments.append(discord.File(BytesIO(icon_content), filename="mc_icon.png"))
            embed.set_thumbnail(url="attachment://mc_icon.png")

        embed.set_footer(text=f"Status: Online\nVersion: {version}\nPlayers: {current_players}/{max_players}")
        await ctx.reply(files=attachments, embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Game(bot))
