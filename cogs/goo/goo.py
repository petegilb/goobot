import os
import re
import logging
import random
import datetime
import asyncio
from typing import TYPE_CHECKING, List, Dict

import asyncpg
import discord
from tabulate import tabulate
from discord.ext import commands, tasks
from src.models.user import User, init_user, increment_user_field, update_user, get_user, get_all_users
from src.models.stat import get_stats, set_goo_lord, set_biggest_loser

if TYPE_CHECKING:
    # from discord.ext.commands._types import BotT
    from main import GooBot

logger = logging.getLogger('discord')

GOO_CHANNELS = [int(channel) for channel in os.getenv('GOO_CHANNELS', []).split(',')]
GOO_CHANCE = 5
GOO_COOLDOWN = 180
GOO_LONG_COOLDOWN_CHANCE = 1
GOO_LONG_COOLDOWN = 60*60
GOOLORD_ROLE_ID = int(os.getenv('GOOLORD_ROLE_ID'))
GOO_JAIL_ROLE_ID = int(os.getenv('GOO_JAIL_ROLE_ID'))
GUILD_ID= int(os.getenv('GUILD_ID'))

# NOTE: DEBUG VALUES!!!
# GOO_CHANCE = 100
# GOO_COOLDOWN = 1
# GOO_LONG_COOLDOWN_CHANCE = 100
# GOO_LONG_COOLDOWN = 60

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
        self.free_from_jail.start()
        asyncio.create_task(self.check_existing_goolord())

    def cog_unload(self):
        self.free_from_jail.cancel()

    async def check_existing_goolord(self):
        """
        If the db has been reset, remove any existing goo lords!
        """
        logger.info("Checking to see if there are any existing goo lords not in the db")
        while self.bot.db_pool is None:
            await asyncio.sleep(6)
        channel: discord.guild.GuildChannel = self.bot.get_channel(GOO_CHANNELS[0])
        guild: discord.Guild = self.bot.get_guild(GUILD_ID)
        goolord_role = guild.get_role(GOOLORD_ROLE_ID)
        if not (goolord_role and guild and channel):
            return
        
        current_goolord = await get_goolord(self.bot.db_pool)
        for goolord in goolord_role.members:
            if current_goolord is not None and current_goolord.id == goolord.id:
                continue
            await goolord.remove_roles(goolord_role)
            await channel.send(f"<@{goolord.id}>, I have no record of any goo lord, so I am removing your role!")

    @tasks.loop(seconds=5.0)
    async def free_from_jail(self):
        channel: discord.guild.GuildChannel = self.bot.get_channel(GOO_CHANNELS[0])
        guild = self.bot.get_guild(GUILD_ID)
        jail_role = guild.get_role(GOO_JAIL_ROLE_ID)
        if not (channel and guild and jail_role):
            return
        
        # free all users not stored in memory...
        for member in jail_role.members:
            if self.bot.jail_counter.get(member.id):
                continue
            await member.remove_roles(jail_role)
            await channel.send(f"<@{member.id}>, you are now free from jail.")

        # free all users stored in memory
        new_jail_counter = self.bot.jail_counter.copy()
        for user_id in self.bot.jail_counter.keys():
            if self.bot.jail_counter[user_id] <= datetime.datetime.now():
                user = guild.get_member(user_id)
                new_jail_counter.pop(user_id)
                await user.remove_roles(jail_role)
                await channel.send(f"<@{user_id}>, you are now free from jail.")
        
        # TODO add an asyncio lock for this? 
        self.bot.jail_counter = new_jail_counter
    
    @commands.cooldown(1, GOO_COOLDOWN, commands.BucketType.user)
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
            await ctx.reply("My liege, you are already the lord of goo. One cannet overthrow thyself.")
            return

        # check if we become the next goo lord
        roll = random.randint(1, 100)
        if roll <= GOO_CHANCE:
            updates['win_count'] = user.win_count + 1
            updates['loss_count'] = 0
            updates['last_win'] = now
            response = WIN_MESSAGE.format(name, user.loss_count)

            # update the goo lord to the new lord!
            await set_goo_lord(pool, member.id, now, ctx)

            # assign goo lord role
            goolord_role = ctx.guild.get_role(GOOLORD_ROLE_ID)
            if current_lord is not None:
                current_lord_disc = ctx.guild.get_member(current_lord.id)
                if current_lord_disc:
                    await current_lord_disc.remove_roles(goolord_role)
            await member.add_roles(goolord_role)

            # update the lord_time of the current goo lord (if they exist)
            if current_lord is None:
                await ctx.send(f"You are the first one to rise up and lead the kingdom of goo! All hail the first goo lord, {name}!")
            else:
                old_lord = current_lord
                reign_time = datetime.datetime.now(tz=datetime.timezone.utc) - old_lord.last_win
                old_lord_lord_time = old_lord.lord_time + reign_time.total_seconds()
                old_lord_updates = {
                    'lord_time': old_lord_lord_time
                }

                await update_user(pool, old_lord.id, old_lord_updates)
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
            
            # send to goo jail if 1 percent chance is hit
            roll = random.randint(1, 100)
            if roll <= GOO_LONG_COOLDOWN_CHANCE:
                goo_jail_role = ctx.guild.get_role(GOO_JAIL_ROLE_ID)
                self.bot.jail_counter[member.id] = datetime.datetime.now() + datetime.timedelta(seconds=GOO_LONG_COOLDOWN)
                await member.add_roles(goo_jail_role)
                await ctx.reply(f"Your attempt to overthrow the lord has been thwarted. You have been sent to jail for {round(GOO_LONG_COOLDOWN / 60)} minutes!")

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
    
    @commands.command()
    @commands.check(is_goo_channel)
    async def gooreign(self, ctx: commands.Context, in_user = None, member: discord.Member = None):
        reign_user = None
        if in_user is not None and in_user:
            if "<" in str(in_user):
                numbers_list = re.findall(r'\d+', str(in_user))
                numbers_only = "".join(numbers_list)
                in_user_id = int(numbers_only)
                reign_user = ctx.guild.get_member(in_user_id)
            else:
                reign_user = ctx.guild.get_member_named(str(in_user))
            
            if reign_user is None:
                await ctx.reply(f"Couldn't find user: {in_user}", delete_after=10)
                return
        else:
            reign_user = ctx.author
        
        current_goolord = await get_goolord(self.bot.db_pool)
        reign_user_name = reign_user.nick if reign_user.nick is not None else reign_user.name

        db_reign_user = await get_user(self.bot.db_pool, reign_user.id)

        # if the user doesnt exist in the db or they have no wins
        if db_reign_user is None or db_reign_user.win_count <= 0:
            response = f"Ah yes, the peasant, {reign_user_name}, has never ruled."
            await ctx.reply(response)
            return

        num_wins = db_reign_user.win_count
        win_time = db_reign_user.lord_time

        # if the user is the current goo lord, calculate the total time (including the current reign)
        if current_goolord.id == reign_user.id:
            current_reign = datetime.datetime.now(tz=datetime.timezone.utc) - db_reign_user.last_win
            win_time = db_reign_user.lord_time + current_reign.total_seconds()

        win_time = round(float(win_time)/60, 2)

        response = f"Ah yes, the glorious reign of {reign_user_name} the great."
        response = f"{response}\nThey have ruled over the kingdom of Flan {num_wins} time(s) for a total time of: {win_time} minute(s)."

        await ctx.reply(response)

    @commands.command()
    @commands.check(is_goo_channel)
    async def gooleaderboard(self, ctx: commands.Context, *, member: discord.Member = None):
        stats = await get_stats(self.bot.db_pool)
        current_goolord = await get_goolord(self.bot.db_pool, stats)
        biggest_loser = await get_biggest_loser(self.bot.db_pool, stats)

        db_users = await get_all_users(self.bot.db_pool)
        discord_users = [ctx.guild.get_member(db_user.id) for db_user in db_users]

        # calculate current lord time
        if current_goolord is not None:
            for idx in range(len(db_users)):
                user = db_users[idx]
                if  user.id != current_goolord.id:
                    continue
                reign_time = datetime.datetime.now(tz=datetime.timezone.utc) - user.last_win
                db_users[idx].lord_time = user.lord_time + reign_time.total_seconds()
            
        discord_users_dict: Dict[int, discord.Member] = {}
        # add discord_users to dict for easier retrieval
        for user in discord_users:
            discord_users_dict[user.id] = user

        sorted_db_users = sorted(db_users, key=lambda user: user.lord_time, reverse=True)

        headers = ["User", "Time", "Win", "Loss", "Hops"]
        data = []

        for db_user in sorted_db_users:
            if db_user is None:
                continue
            user = discord_users_dict.get(db_user.id)
            if user is None:
                continue
            
            name = user.nick if user.nick else user.name
            if current_goolord is not None and current_goolord.id == db_user.id:
                name = f"[32m{name}[0m"
            elif biggest_loser is not None and biggest_loser.id == db_user.id:
                name = f"[31m{name}[0m"
            lord_time_min = round(float(db_user.lord_time)/60, 2)
            user_data = [name, lord_time_min, db_user.win_count, db_user.loss_count, db_user.hopped_goo]
            data.append(user_data)
        
        table_str = tabulate(data, headers)
        await ctx.send(f"```ansi\n{table_str}```")

    @commands.command()
    @commands.check(is_goo_channel)
    async def gooloser(self, ctx: commands.Context, *, member: discord.Member = None):
        biggest_loser = await get_biggest_loser(self.bot.db_pool)
        if biggest_loser is None:
            await ctx.reply("Nobody is the biggest loser yet! Let's see some goo hopping, peasant!")
        discord_loser = ctx.guild.get_member(biggest_loser.id)
        if discord_loser is None:
            return
        
        name = discord_loser.nick if discord_loser.nick else discord_loser.name
        loser_str = "The goo loser record is held by {0} with the most unsuccessful attempts to overthrow the goo lord. They attempted {1} time(s)."
        await ctx.reply(loser_str.format(name, biggest_loser.loss_count))

    @commands.command()
    @commands.check(is_goo_channel)
    async def goolord(self, ctx: commands.Context, *, member: discord.Member = None):
        current_goolord = await get_goolord(self.bot.db_pool)
        if current_goolord is None:
            await ctx.reply("The throne lies empty...")
            return
        
        reign_time = datetime.datetime.now(tz=datetime.timezone.utc) - current_goolord.last_win
        lord_time = current_goolord.lord_time + reign_time.total_seconds()
        lord_time = round(float(lord_time)/60, 2)
        lord_str = f"the current goo lord is <@{current_goolord.id}>. their current reign has been {lord_time} minute(s)."
        await ctx.reply(lord_str)

    @commands.command()
    @commands.check(is_goo_channel)
    async def gooball(self, ctx: commands.Context, *, member: discord.Member = None):
        embed=discord.Embed(title="GOOOOOOB", color=0xb0ff70)
        embed.set_image(url=f"https://c.tenor.com/UCkUaBYlktkAAAAC/tenor.gif")
        await ctx.reply(embed=embed)

        
async def setup(bot: commands.Bot):
    await bot.add_cog(Goo(bot))
