import logging

import discord
from discord.ext import commands

logger = logging.getLogger('discord')

def is_goo_channel(ctx):
    return ctx.message.channel.id == 1379490638281834626

class Goo(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
    
    @commands.command()
    @commands.check(is_goo_channel)
    async def hopinmygoo(self, ctx, *, member: discord.Member = None):
        member = member or ctx.author
        member = member.nick if member.nick is not None else member
        response_string = (
            f"no please {member} i dont want to hop in your goo"
        )
        await ctx.send(response_string)

async def setup(bot: commands.Bot):
    await bot.add_cog(Goo(bot))
