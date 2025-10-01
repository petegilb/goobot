import logging
import os
import base64
from typing import TYPE_CHECKING
import requests

import asyncpg
import discord
from discord.ext import commands
from src.util import init_tables, ALL_TABLES

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

logger = logging.getLogger('discord')

MC_SERVER_IP=os.getenv("MC_SERVER_IP", "localhost")


class Game(commands.Cog):

    def __init__(self, bot):
        self.bot: GooBot = bot

    @commands.command()
    async def minecraft(self, ctx: commands.Context):
        r = requests.get(f"https://mcapi.us/server/status?ip={MC_SERVER_IP}")
        if r.status_code != 200:
            await ctx.reply(f"Couldn't reach the server at {MC_SERVER_IP}")
            return
        logger.info(r)
        data = r.json()
        players = data.get('players')
        current_players = players.get('now')
        max_players = players.get('max')
        player_names = players.get('sample')
        version = data.get('server').get('name').split(' ')[1]
        status = "Online" if data.get('online') == True else "Offline"
        icon_encoded = data.get('favicon').replace('data:image/png;base64,', '')
        icon_content = base64.b64decode(icon_encoded)

        with open("./mc_icon.png","wb") as f:
            f.write(icon_content)

        player_names_str = "__Players__"
        for player_name in player_names:
            player_names_str+=f"\n{player_name.get('name')}"

        icon_file = discord.File("./mc_icon.png", filename="mc_icon.png")
        embed = discord.Embed(
            title=f"{MC_SERVER_IP}",
            # description=f"{stats.model_dump_json(indent=4)}",
            color=0x39e81a,
        )
        embed.set_thumbnail(url="attachment://mc_icon.png")
        embed.description = player_names_str
        embed.set_footer(
            text=f"Status: {status}\nVersion: {version}\nPlayers: {current_players}/{max_players}", 
            icon_url="attachment://mc_icon.png"
        )
        await ctx.reply(file=icon_file, embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Game(bot))
