import os
import logging
import random
import datetime
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord.ext import commands
from src.models.user import User, init_user, increment_user_field, update_user, get_user
from src.models.stat import get_stats, set_goo_lord, set_biggest_loser

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

logger = logging.getLogger('discord')

GOO_CHANNELS = [int(channel) for channel in os.getenv('GOO_CHANNELS', []).split(',')]
GOO_CHANCE = 5
GOO_COOLDOWN = 180
GOO_LONG_COOLDOWN_CHANCE = 1

LOSS_MESSAGE = "no please {0} i dont want to hop in your goo"
WIN_MESSAGE = """fine...  i'll hop in your goo, {0}...
a new goo lord has been declared!! they attempted to overthrow the lord {1} time(s) before success!"""
BIGGEST_LOSER_MESSAGE = """You have beaten the streak for most attempts to overthrow the goo lord without success with {0} attempt(s)!

The previous loser was <@{1}> with {2} attempt(s)!"""

async def get_goolord(pool: asyncpg.Pool, stats=None) -> User|None:
    if stats is None:
        stats = await get_stats(pool)
    if stats.last_winner_id is None:
        return None
    return await get_user(pool, stats.last_winner_id)

async def get_biggest_loser(pool: asyncpg.Pool, stats=None) -> User|None:
    if stats is None:
        stats = await get_stats(pool)
    if stats.biggest_loser_id is None:
        return None
    return await get_user(pool, stats.biggest_loser_id)

def is_goo_channel(ctx: commands.Context):
    return ctx.message.channel.id in GOO_CHANNELS

class Goo(commands.Cog):

    def __init__(self, bot):
        self.bot: GooBot = bot
    
    @commands.command()
    @commands.check(is_goo_channel)
    async def hopinmygoo(self, ctx: commands.Context, *, member: discord.Member = None):
        member = member or ctx.author
        name = member.nick if member.nick is not None else member.name
        response = "..."
        now = datetime.datetime.now()

        if self.bot.db_pool is None or self.bot.db_pool.is_closing():
            logger.error("db pool doesn't exist or is closing")
            return
        
        pool = self.bot.db_pool
        user = await init_user(pool, ctx, member)
        logger.info(f"Retrieved/created user: {user}")

        updates = {
            'hopped_goo': user.hopped_goo+1,
        }
        if name != user.username:
            updates['username'] = name

        stats = await get_stats(pool)
        current_lord = await get_goolord(pool, stats)
        if current_lord is not None and current_lord.id == member.id:
            await ctx.send("My liege, you are already the lord of goo. One cannet overthrow thyself.")
            return

        # check if we become the next goo lord
        roll = random.randint(1, 100)
        if roll <= GOO_CHANCE:
            updates['win_count'] = user.win_count + 1
            updates['loss_count'] = 0
            updates['last_win'] = now
            response = WIN_MESSAGE.format(name, user.loss_count)

            # update the goo lord to the new lord!
            await set_goo_lord(pool, member.id, now)

            # update the lord_time of the current goo lord (if they exist)
            if current_lord is None:
                await ctx.send(f"You are the first one to rise up and lead the kingdom of goo! All hail the first goo lord, {name}!")
            else:
                old_lord = current_lord
                reign_time = now - old_lord.last_win
                old_lord_lord_time = old_lord.lord_time + reign_time.total_seconds()
                old_lord_updates = {
                    'lord_time': old_lord_lord_time
                }

                update_user(pool, old_lord.id, old_lord_updates)
                response = f"{response}\nthe last goo lord was <@{current_lord.id}>. they were lord for {round(float(old_lord_lord_time)/60, ndigits=2)} minutes(s)."
            
            await ctx.send(response)
        else:
            updates['loss_count'] = user.loss_count + 1
            response = LOSS_MESSAGE.format(name)
            await ctx.send(response)
            # check for biggest loser!
            biggest_loser = await get_biggest_loser(pool, stats)
            if biggest_loser and biggest_loser.loss_count < updates['loss_count'] and biggest_loser.id != member.id:
                await set_biggest_loser(pool, member.id)
                response = BIGGEST_LOSER_MESSAGE.format(updates['loss_count'], biggest_loser.id, biggest_loser.loss_count)
                await ctx.send(response)
            if biggest_loser is None:
                logger.debug("setting first biggest loser")
                await set_biggest_loser(pool, member.id)
        await update_user(pool, member.id, updates)
    
    @commands.command()
    @commands.check(is_goo_channel)
    async def goostats(self, ctx: commands.Context, *, member: discord.Member = None):
        if self.bot.db_pool is None or self.bot.db_pool.is_closing():
            logger.error("db pool doesn't exist or is closing")
            return
        
        pool = self.bot.db_pool
        stats = await get_stats(pool)
        logger.info(f"Retrieved stats: {stats}")

        embed = discord.Embed(
            title="🐌 Goo Stats 🐌",
            description=f"{stats.model_dump_json(indent=4)}",
            color=0x39e81a,
        )
        await ctx.send(embed=embed)
        
async def setup(bot: commands.Bot):
    await bot.add_cog(Goo(bot))
