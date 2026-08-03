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
from src.models.user import User, init_user, record_win, record_loss, update_user, get_user, get_all_users
from src.models.stat import Stat, get_stats, set_goo_lord, set_biggest_loser, use_banish, set_decree

if TYPE_CHECKING:
    from main import GooBot

logger = logging.getLogger('discord')


def parse_id_list(raw: str) -> List[int]:
    return [int(part.strip()) for part in raw.split(',') if part.strip()]


def require_id_env(name: str) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is not set, check your .env")
    return int(value.strip())


GOO_CHANNELS = parse_id_list(os.getenv('GOO_CHANNELS', ''))
GOO_CHANCE = 5
GOO_COOLDOWN = 180
GOO_LONG_COOLDOWN_CHANCE = 1
GOO_LONG_COOLDOWN = 60*60
BANISH_DURATION = 60*60
DECREE_COOLDOWN = 300
MAX_DECREE_LENGTH = 200
# discord truncates a custom status past this
MAX_STATUS_LENGTH = 128
MAX_TOPIC_LENGTH = 1024
GOOLORD_ROLE_ID = require_id_env('GOOLORD_ROLE_ID')
GOO_JAIL_ROLE_ID = require_id_env('GOO_JAIL_ROLE_ID')
GUILD_ID = require_id_env('GUILD_ID')

if not GOO_CHANNELS:
    raise RuntimeError("GOO_CHANNELS is empty, check your .env")

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
BANISH_MESSAGE = "By decree of the goo lord, <@{0}> has been banished to goo jail for {1} minute(s)!"
DECREE_MESSAGE = "📜 the goo lord has decreed:\n> {0}"


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


def reign_seconds_since(last_win: datetime.datetime|None) -> float:
    """
    A goo lord can have no last_win if their reign predates the stored history.
    """
    if last_win is None:
        return 0.0
    return (utc_now() - last_win).total_seconds()


def to_minutes(seconds: float) -> float:
    return round(float(seconds)/60, 2)


def total_lord_seconds(db_user: User, current_goolord: User|None) -> float:
    if current_goolord is not None and current_goolord.id == db_user.id:
        return db_user.lord_time + reign_seconds_since(db_user.last_win)
    return db_user.lord_time


async def get_goolord(pool: asyncpg.Pool, stats: Stat|None = None) -> User|None:
    if stats is None:
        stats = await get_stats(pool)
    if stats is None or stats.last_winner_id is None:
        return None
    return await get_user(pool, stats.last_winner_id)

async def get_biggest_loser(pool: asyncpg.Pool, stats: Stat|None = None) -> User|None:
    if stats is None:
        stats = await get_stats(pool)
    if stats is None or stats.biggest_loser_id is None:
        return None
    return await get_user(pool, stats.biggest_loser_id)

def is_goo_channel(ctx: commands.Context):
    return ctx.message.channel.id in GOO_CHANNELS

class Goo(commands.Cog):

    def __init__(self, bot):
        self.bot: GooBot = bot
        self.free_from_jail.start()
        self.startup_check = asyncio.create_task(self.check_existing_goolord())

    def cog_unload(self):
        self.free_from_jail.cancel()
        self.startup_check.cancel()

    def get_pool(self) -> asyncpg.Pool|None:
        if self.bot.db_pool is None or self.bot.db_pool.is_closing():
            logger.error("db pool doesn't exist or is closing")
            return None
        return self.bot.db_pool

    async def check_existing_goolord(self):
        """
        If the db has been reset, remove any existing goo lords!
        """
        await self.bot.wait_until_ready()
        logger.info("Checking to see if there are any existing goo lords not in the db")
        pool = self.get_pool()
        if pool is None:
            return

        # the channel topic survives a restart on its own, the bot status does not
        stats = await get_stats(pool)
        if stats is not None and stats.decree:
            await self.set_decree_status(stats.decree)

        guild = self.bot.get_guild(GUILD_ID)
        channel = self.bot.get_channel(GOO_CHANNELS[0])
        if guild is None or channel is None:
            logger.error("Could not find the configured guild or goo channel")
            return
        goolord_role = guild.get_role(GOOLORD_ROLE_ID)
        if goolord_role is None:
            logger.error(f"Could not find the goo lord role {GOOLORD_ROLE_ID}")
            return

        current_goolord = await get_goolord(pool)
        for goolord in goolord_role.members:
            if current_goolord is not None and current_goolord.id == goolord.id:
                continue
            try:
                await goolord.remove_roles(goolord_role)
            except discord.HTTPException:
                logger.exception(f"Failed to remove the goo lord role from {goolord.id}")
                continue
            await channel.send(f"<@{goolord.id}>, I have no record of any goo lord, so I am removing your role!")

    @tasks.loop(seconds=5.0)
    async def free_from_jail(self):
        guild = self.bot.get_guild(GUILD_ID)
        channel = self.bot.get_channel(GOO_CHANNELS[0])
        if guild is None or channel is None:
            return
        jail_role = guild.get_role(GOO_JAIL_ROLE_ID)
        if jail_role is None:
            return

        now = utc_now()
        # the role is the source of truth, so members who left the guild are skipped
        for member in list(jail_role.members):
            release_at = self.bot.jail_counter.get(member.id)
            if release_at is not None and release_at > now:
                continue
            self.bot.jail_counter.pop(member.id, None)
            try:
                await member.remove_roles(jail_role)
            except discord.HTTPException:
                logger.exception(f"Failed to remove the goo jail role from {member.id}")
                continue
            await channel.send(f"<@{member.id}>, you are now free from jail.")

        expired = [user_id for user_id, release_at in self.bot.jail_counter.items() if release_at <= now]
        for user_id in expired:
            self.bot.jail_counter.pop(user_id, None)

    @free_from_jail.before_loop
    async def before_free_from_jail(self):
        await self.bot.wait_until_ready()

    @free_from_jail.error
    async def free_from_jail_error(self, error: Exception):
        # without this the loop would stay dead and nobody would ever leave jail
        logger.error("goo jail loop failed, restarting it", exc_info=error)
        self.free_from_jail.restart()

    @commands.cooldown(1, GOO_COOLDOWN, commands.BucketType.user)
    @commands.command()
    @commands.check(is_goo_channel)
    async def hopinmygoo(self, ctx: commands.Context):
        pool = self.get_pool()
        if pool is None:
            ctx.command.reset_cooldown(ctx)
            return

        member = ctx.author
        user = await init_user(pool, member)
        logger.info(f"Retrieved/created user: {user}")

        stats = await get_stats(pool)
        if stats is None:
            logger.error("the stats row is missing")
            ctx.command.reset_cooldown(ctx)
            return

        current_lord = await get_goolord(pool, stats)
        if current_lord is not None and current_lord.id == member.id:
            ctx.command.reset_cooldown(ctx)
            await ctx.reply("My liege, you are already the lord of goo. One cannet overthrow thyself.")
            return

        roll = random.randint(1, 100)
        if roll <= GOO_CHANCE:
            await self.handle_win(ctx, pool, user, current_lord)
        else:
            await self.handle_loss(ctx, pool, stats)

    async def handle_win(self, ctx: commands.Context, pool: asyncpg.Pool, user: User, current_lord: User|None):
        member = ctx.author
        name = member.display_name
        attempts_before_win = user.loss_count
        now = utc_now()

        # the db is written before any role change so a failed role edit cannot
        # leave stats pointing at a lord with no last_win
        await record_win(pool, member.id, name, now)
        await set_goo_lord(pool, member.id, now)

        response = WIN_MESSAGE.format(name, attempts_before_win)

        if current_lord is None:
            await ctx.send(f"You are the first one to rise up and lead the kingdom of goo! All hail the first goo lord, {name}!")
        else:
            reign_seconds = reign_seconds_since(current_lord.last_win)
            await update_user(pool, current_lord.id, {'lord_time': int(current_lord.lord_time + reign_seconds)})
            response = f"{response}\nthe last goo lord was <@{current_lord.id}>. they were lord for {to_minutes(reign_seconds)} minutes(s)."

        await self.move_goolord_role(ctx.guild, current_lord, member)
        await ctx.send(response)

    async def move_goolord_role(self, guild: discord.Guild, previous_lord: User|None, new_lord: discord.Member):
        goolord_role = guild.get_role(GOOLORD_ROLE_ID)
        if goolord_role is None:
            logger.error(f"Could not find the goo lord role {GOOLORD_ROLE_ID}")
            return
        try:
            if previous_lord is not None:
                previous_member = guild.get_member(previous_lord.id)
                if previous_member is not None:
                    await previous_member.remove_roles(goolord_role)
            await new_lord.add_roles(goolord_role)
        except discord.HTTPException:
            logger.exception("Failed to move the goo lord role")

    async def handle_loss(self, ctx: commands.Context, pool: asyncpg.Pool, stats: Stat):
        member = ctx.author
        user = await record_loss(pool, member.id, member.display_name)
        await ctx.send(LOSS_MESSAGE.format(member.display_name))

        if user is not None:
            await self.check_biggest_loser(ctx, pool, stats, user.loss_count)
        await self.maybe_send_to_jail(ctx)

    async def check_biggest_loser(self, ctx: commands.Context, pool: asyncpg.Pool, stats: Stat, loss_count: int):
        # stats was read before the loss was recorded, so this is the previous holder
        previous_loser = await get_biggest_loser(pool, stats)
        if stats.biggest_loser_id is not None and stats.biggest_loser_count >= loss_count:
            return

        member = ctx.author
        await set_biggest_loser(pool, member.id, loss_count)
        if previous_loser is not None and previous_loser.id != member.id:
            await ctx.send(BIGGEST_LOSER_MESSAGE.format(loss_count, previous_loser.id, stats.biggest_loser_count))

    async def maybe_send_to_jail(self, ctx: commands.Context):
        roll = random.randint(1, 100)
        if roll > GOO_LONG_COOLDOWN_CHANCE:
            return

        goo_jail_role = ctx.guild.get_role(GOO_JAIL_ROLE_ID)
        if goo_jail_role is None:
            logger.error(f"Could not find the goo jail role {GOO_JAIL_ROLE_ID}")
            return

        member = ctx.author
        self.bot.jail_counter[member.id] = utc_now() + datetime.timedelta(seconds=GOO_LONG_COOLDOWN)
        try:
            await member.add_roles(goo_jail_role)
        except discord.HTTPException:
            logger.exception(f"Failed to add the goo jail role to {member.id}")
            self.bot.jail_counter.pop(member.id, None)
            return
        await ctx.reply(f"Your attempt to overthrow the lord has been thwarted. You have been sent to jail for {round(GOO_LONG_COOLDOWN / 60)} minutes!")

    async def require_goolord(self, ctx: commands.Context, pool: asyncpg.Pool) -> User|None:
        current_lord = await get_goolord(pool)
        if current_lord is None:
            await ctx.reply("The throne lies empty. Only a goo lord may do that.")
            return None
        if current_lord.id != ctx.author.id:
            await ctx.reply(f"Only the goo lord may do that, peasant. Kneel before <@{current_lord.id}>.")
            return None
        return current_lord

    @commands.command()
    @commands.check(is_goo_channel)
    async def banish(self, ctx: commands.Context, *, member: discord.Member = None):
        pool = self.get_pool()
        if pool is None:
            return

        stats = await get_stats(pool)
        if stats is None:
            logger.error("the stats row is missing")
            return
        if await self.require_goolord(ctx, pool) is None:
            return
        if stats.banish_used:
            await ctx.reply("You have already banished someone this reign. Even a goo lord has limits.")
            return

        if member is None:
            await ctx.reply("Banish which peasant? Try `!banish @someone`")
            return
        if member.id == ctx.author.id:
            await ctx.reply("Banishing yourself would be a strange way to rule.")
            return
        if member.bot:
            await ctx.reply("You cannot banish a machine.")
            return

        jail_role = ctx.guild.get_role(GOO_JAIL_ROLE_ID)
        if jail_role is None:
            logger.error(f"Could not find the goo jail role {GOO_JAIL_ROLE_ID}")
            await ctx.reply("The goo jail seems to have vanished. Tell an admin.")
            return
        if jail_role in member.roles:
            await ctx.reply(f"{member.display_name} is already rotting in goo jail.")
            return

        self.bot.jail_counter[member.id] = utc_now() + datetime.timedelta(seconds=BANISH_DURATION)
        try:
            await member.add_roles(jail_role)
        except discord.HTTPException:
            logger.exception(f"Failed to banish {member.id}")
            self.bot.jail_counter.pop(member.id, None)
            await ctx.reply("The banishment failed. Perhaps they are beyond your reach.")
            return

        # only spent once the banishment actually landed
        await use_banish(pool)
        await ctx.send(BANISH_MESSAGE.format(member.id, round(BANISH_DURATION / 60)))

    @commands.cooldown(1, DECREE_COOLDOWN, commands.BucketType.channel)
    @commands.command()
    @commands.check(is_goo_channel)
    async def decree(self, ctx: commands.Context, *, text: str = None):
        pool = self.get_pool()
        if pool is None:
            ctx.command.reset_cooldown(ctx)
            return

        if await self.require_goolord(ctx, pool) is None:
            ctx.command.reset_cooldown(ctx)
            return

        text = (text or "").strip()
        if not text:
            ctx.command.reset_cooldown(ctx)
            await ctx.reply("A decree needs words. Try `!decree <your proclamation>`")
            return
        if len(text) > MAX_DECREE_LENGTH:
            ctx.command.reset_cooldown(ctx)
            await ctx.reply(f"That decree runs long. Keep it under {MAX_DECREE_LENGTH} characters.")
            return

        await set_decree(pool, text)
        await self.set_decree_status(text)
        await self.set_decree_topic(ctx.channel, text)
        await ctx.send(DECREE_MESSAGE.format(text))

    async def set_decree_status(self, text: str):
        try:
            await self.bot.change_presence(
                status=discord.Status.online,
                activity=discord.CustomActivity(name=text[:MAX_STATUS_LENGTH])
            )
        except discord.HTTPException:
            logger.exception("Failed to set the bot status from the decree")

    async def set_decree_topic(self, channel: discord.abc.GuildChannel, text: str):
        # channel topic edits are heavily rate limited, hence the command cooldown
        try:
            await channel.edit(topic=text[:MAX_TOPIC_LENGTH])
        except discord.Forbidden:
            logger.error("Missing Manage Channels, cannot set the goo channel topic")
        except discord.HTTPException:
            logger.exception("Failed to set the goo channel topic from the decree")

    @commands.command()
    @commands.check(is_goo_channel)
    async def goostats(self, ctx: commands.Context):
        pool = self.get_pool()
        if pool is None:
            return

        stats = await get_stats(pool)
        if stats is None:
            await ctx.reply("There are no goo stats yet.")
            return
        logger.info(f"Retrieved stats: {stats}")

        embed = discord.Embed(
            title="🐌 Goo Stats 🐌",
            description=f"{stats.model_dump_json(indent=4)}",
            color=0x39e81a,
        )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.check(is_goo_channel)
    async def gooreign(self, ctx: commands.Context, in_user = None):
        pool = self.get_pool()
        if pool is None:
            return

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

        current_goolord = await get_goolord(pool)
        reign_user_name = reign_user.display_name

        db_reign_user = await get_user(pool, reign_user.id)

        # if the user doesnt exist in the db or they have no wins
        if db_reign_user is None or db_reign_user.win_count <= 0:
            response = f"Ah yes, the peasant, {reign_user_name}, has never ruled."
            await ctx.reply(response)
            return

        num_wins = db_reign_user.win_count
        win_time = to_minutes(total_lord_seconds(db_reign_user, current_goolord))

        response = f"Ah yes, the glorious reign of {reign_user_name} the great."
        response = f"{response}\nThey have ruled over the kingdom of Flan {num_wins} time(s) for a total time of: {win_time} minute(s)."

        await ctx.reply(response)

    @commands.command()
    @commands.check(is_goo_channel)
    async def gooleaderboard(self, ctx: commands.Context):
        pool = self.get_pool()
        if pool is None:
            return

        stats = await get_stats(pool)
        current_goolord = await get_goolord(pool, stats)
        biggest_loser = await get_biggest_loser(pool, stats)

        db_users = await get_all_users(pool)

        # members who have left the guild are dropped rather than shown as blanks
        members_by_id: Dict[int, discord.Member] = {}
        for db_user in db_users:
            member = ctx.guild.get_member(db_user.id)
            if member is not None:
                members_by_id[db_user.id] = member

        lord_seconds_by_id = {db_user.id: total_lord_seconds(db_user, current_goolord) for db_user in db_users}
        sorted_db_users = sorted(db_users, key=lambda db_user: lord_seconds_by_id[db_user.id], reverse=True)

        headers = ["User", "Time", "Win", "Loss", "Hops", "LS"]
        data = []

        for db_user in sorted_db_users:
            member = members_by_id.get(db_user.id)
            if member is None:
                continue

            name = member.display_name
            if current_goolord is not None and current_goolord.id == db_user.id:
                name = f"[32m{name}[0m"
            elif biggest_loser is not None and biggest_loser.id == db_user.id:
                name = f"[31m{name}[0m"
            lord_time_min = to_minutes(lord_seconds_by_id[db_user.id])
            user_data = [name, lord_time_min, db_user.win_count, db_user.loss_count, db_user.hopped_goo, db_user.loss_streak]
            data.append(user_data)

        table_str = tabulate(data, headers)
        await ctx.send(f"```ansi\n{table_str}```")

    @commands.command()
    @commands.check(is_goo_channel)
    async def gooloser(self, ctx: commands.Context):
        pool = self.get_pool()
        if pool is None:
            return

        stats = await get_stats(pool)
        biggest_loser = await get_biggest_loser(pool, stats)
        if biggest_loser is None:
            await ctx.reply("Nobody is the biggest loser yet! Let's see some goo hopping, peasant!")
            return

        discord_loser = ctx.guild.get_member(biggest_loser.id)
        name = discord_loser.display_name if discord_loser is not None else biggest_loser.username

        loser_str = "The goo loser record is held by {0} with the most unsuccessful attempts to overthrow the goo lord. They attempted {1} time(s)."
        await ctx.reply(loser_str.format(name, stats.biggest_loser_count))

    @commands.command()
    @commands.check(is_goo_channel)
    async def goolord(self, ctx: commands.Context):
        pool = self.get_pool()
        if pool is None:
            return

        current_goolord = await get_goolord(pool)
        if current_goolord is None:
            await ctx.reply("The throne lies empty...")
            return

        lord_time = to_minutes(reign_seconds_since(current_goolord.last_win))
        lord_str = f"the current goo lord is <@{current_goolord.id}>. their current reign has been {lord_time} minute(s)."
        await ctx.reply(lord_str)

    @commands.command()
    async def gooball(self, ctx: commands.Context):
        embed = discord.Embed(title="GOOOOOOB", color=0xb0ff70)
        embed.set_image(url="https://c.tenor.com/UCkUaBYlktkAAAAC/tenor.gif")
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Goo(bot))
