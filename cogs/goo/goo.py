import os
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from src.models.user import User, init_user

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

logger = logging.getLogger('discord')

GOO_CHANNELS = [int(channel) for channel in os.getenv('GOO_CHANNELS', []).split(',')]

def is_goo_channel(ctx: commands.Context):
    return ctx.message.channel.id in GOO_CHANNELS

class Goo(commands.Cog):

    def __init__(self, bot):
        self.bot: GooBot = bot
    
    @commands.command()
    @commands.check(is_goo_channel)
    async def hopinmygoo(self, ctx: commands.Context, *, member: discord.Member = None):
        member = member or ctx.author
        member = member.nick if member.nick is not None else member
        response_string = (
            f"no please {member} i dont want to hop in your goo"
        )

        if self.bot.db_pool is None or self.bot.db_pool.is_closing():
            logger.error("db pool doesn't exist or is closing")
            return
        
        pool = self.bot.db_pool
        user = await init_user(pool, ctx, member)
        logger.info(f"Retrieved/created user: {user}")
        
        await ctx.send(response_string)

async def setup(bot: commands.Bot):
    await bot.add_cog(Goo(bot))
