import logging

import discord
from discord.ext import commands

logger = logging.getLogger('discord')

class Greet(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self._last_member = None
    
    @commands.command()
    async def hello(self, ctx, *, member: discord.Member = None):
        """Says hello"""
        member = member or ctx.author
        if self._last_member is None or self._last_member.id != member.id:
            await ctx.send(f'Hello {member.name}~')
        else:
            await ctx.send(f'Hello {member.name}... This feels familiar.')
        self._last_member = member

async def setup(bot: commands.Bot):
    await bot.add_cog(Greet(bot))
