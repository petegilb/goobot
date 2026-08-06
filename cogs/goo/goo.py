import os
import re
import logging
import random
import datetime
import asyncio
from typing import TYPE_CHECKING, List, Dict, NamedTuple, Set, Tuple

import asyncpg
import discord
from tabulate import tabulate
from discord.ext import commands, tasks
from src.jail import JailSentence
from src.models.user import User, init_user, record_win, record_loss, claim_daily_duel, update_user, get_user, get_all_users
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


def optional_id_env(name: str) -> int|None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return int(value.strip())


GOO_CHANNELS = parse_id_list(os.getenv('GOO_CHANNELS', ''))
GOO_CHANCE = 5
GOO_COOLDOWN = 180
GOO_LONG_COOLDOWN_CHANCE = 1
GOO_LONG_COOLDOWN = 60*60
BANISH_DURATION = 60*60
DUEL_CHANCE = 33
DUEL_COOLDOWN = 24*60*60
DUEL_LOSS_JAIL = 90*60
DECREE_COOLDOWN = 300
MAX_DECREE_LENGTH = 200
JAILBREAK_MIN_CHANCE = 1
JAILBREAK_MAX_CHANCE = 10
JAILBREAK_FOLLOW_CHANCE = 50
JAILBREAK_COOLDOWN = 5*60
JAILBREAK_PENALTY = 10*60
# discord truncates a custom status past this
MAX_STATUS_LENGTH = 128
MAX_TOPIC_LENGTH = 1024
GOOLORD_ROLE_ID = require_id_env('GOOLORD_ROLE_ID')
GOO_JAIL_ROLE_ID = require_id_env('GOO_JAIL_ROLE_ID')
GOO_JAIL_CHANNEL_ID = require_id_env('GOO_JAIL_CHANNEL_ID')
GENERAL_CHANNEL_ID = optional_id_env('GENERAL_CHANNEL_ID')
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
DUEL_WIN_MESSAGE = """⚔️ {0} draws steel against the goo lord... and wins!
the throne is taken by force!"""
DUEL_LOSS_MESSAGE = """⚔️ {0} draws steel against the goo lord... and is struck down!
the lord stands firm. to the goo jail with them for {1} minute(s)."""
JAILBREAK_FAIL_MESSAGE = "you throw yourself at the bars and bounce off. ({0}% chance, {1} of your sentence left)"
JAILBREAK_OPEN_MESSAGE = "🔓 **JAILBREAK** 🔓\n<@{0}> works the lock loose and slips into the night!"
JAILBREAK_FOLLOW_MESSAGE = "> <@{0}> ({1} left to serve) bolts through the open door behind them!"
JAILBREAK_CAUGHT_MESSAGE = "> <@{0}> ({1} left to serve) is dragged back by the guards. the jail is secured."
JAILBREAK_EMPTY_MESSAGE = "the cells behind them are empty. nobody else was there for the ride."
JAILBREAK_CLEARED_MESSAGE = "the goo jail stands empty. every last prisoner is loose."
JAILBREAK_PENALTY_MESSAGE = "{0} prisoner(s) stay behind, and earn {1} more minute(s) for the trouble."
JAILBREAK_ANNOUNCE_MESSAGE = "🔓 word reaches the kingdom of goo: {0} broke out of goo jail!"


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


def format_countdown(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return "less than a minute"


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


def is_jail_channel(ctx: commands.Context):
    return ctx.message.channel.id == GOO_JAIL_CHANNEL_ID


def jailbreak_chance(sentence: JailSentence, now: datetime.datetime) -> int:
    span = JAILBREAK_MAX_CHANCE - JAILBREAK_MIN_CHANCE
    return round(JAILBREAK_MIN_CHANCE + span * sentence.fraction_remaining(now))


class ConfigProblem(NamedTuple):
    # 'error' means a feature is broken, 'warning' means it is degraded
    severity: str
    message: str

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

    def check_role(self, guild: discord.Guild, bot_member: discord.Member, role_id: int, label: str) -> List[ConfigProblem]:
        role = guild.get_role(role_id)
        if role is None:
            return [ConfigProblem('error', f"{label} {role_id} does not exist in {guild.name}.")]

        problems = []
        if role.managed:
            problems.append(ConfigProblem('error', f"{label} '{role.name}' is managed by an integration and can never be assigned."))
        if role >= bot_member.top_role:
            problems.append(ConfigProblem('error', f"{label} '{role.name}' sits at or above the bot's highest role, so the bot cannot assign it."))
        return problems

    def resolve_channel(self, guild: discord.Guild, channel_id: int, label: str) -> Tuple[discord.TextChannel|None, List[ConfigProblem]]:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return None, [ConfigProblem('error', f"{label} {channel_id} does not exist, or the bot cannot see it.")]
        if not isinstance(channel, discord.TextChannel):
            return None, [ConfigProblem('error', f"{label} {channel_id} is not a text channel.")]
        if channel.guild.id != guild.id:
            return None, [ConfigProblem('error', f"{label} #{channel.name} belongs to a different server than GUILD_ID.")]
        return channel, []

    def check_bot_can_post(self, channel: discord.TextChannel, bot_member: discord.Member) -> List[ConfigProblem]:
        permissions = channel.permissions_for(bot_member)
        required = [('view_channel', 'View Channel'), ('send_messages', 'Send Messages'), ('embed_links', 'Embed Links')]
        return [
            ConfigProblem('error', f"Missing {label} in #{channel.name}.")
            for attribute, label in required
            if not getattr(permissions, attribute)
        ]

    def check_channel(self, guild: discord.Guild, bot_member: discord.Member, jail_role: discord.Role|None, channel_id: int) -> List[ConfigProblem]:
        channel, problems = self.resolve_channel(guild, channel_id, 'Goo channel')
        if channel is None:
            return problems

        problems.extend(self.check_bot_can_post(channel, bot_member))
        if not channel.permissions_for(bot_member).manage_channels:
            problems.append(ConfigProblem('warning', f"Missing Manage Channels in #{channel.name}, so !decree cannot set the topic."))

        # goo jail only works because the role is denied the channel in discord
        if jail_role is not None:
            overwrite = channel.overwrites_for(jail_role)
            if overwrite.view_channel is not False and overwrite.send_messages is not False:
                problems.append(ConfigProblem('warning', f"The goo jail role does not deny access to #{channel.name}, so jailed players can still play."))
        return problems

    def check_jail_channel(self, guild: discord.Guild, bot_member: discord.Member, jail_role: discord.Role|None) -> List[ConfigProblem]:
        channel, problems = self.resolve_channel(guild, GOO_JAIL_CHANNEL_ID, 'GOO_JAIL_CHANNEL_ID')
        if channel is None:
            return problems

        problems.extend(self.check_bot_can_post(channel, bot_member))
        if channel.id in GOO_CHANNELS:
            problems.append(ConfigProblem('error', f"#{channel.name} is both the goo jail channel and a goo channel, so jailed players can still play."))

        # prisoners can only call !jailbreak if the jail role can talk in there
        if jail_role is not None:
            overwrite = channel.overwrites_for(jail_role)
            if overwrite.view_channel is not True or overwrite.send_messages is not True:
                problems.append(ConfigProblem('warning', f"The goo jail role is not explicitly allowed to talk in #{channel.name}, so prisoners may not be able to use !jailbreak."))
        return problems

    def check_general_channel(self, guild: discord.Guild, bot_member: discord.Member) -> List[ConfigProblem]:
        if GENERAL_CHANNEL_ID is None:
            return [ConfigProblem('warning', "GENERAL_CHANNEL_ID is not set, so decrees are only posted where they are made.")]

        channel, problems = self.resolve_channel(guild, GENERAL_CHANNEL_ID, 'GENERAL_CHANNEL_ID')
        if channel is None:
            return problems
        problems.extend(self.check_bot_can_post(channel, bot_member))
        return problems

    def verify_configuration(self) -> List[ConfigProblem]:
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return [ConfigProblem('error', f"GUILD_ID {GUILD_ID} not found. Is the bot invited to that server?")]

        bot_member = guild.me
        if bot_member is None:
            return [ConfigProblem('error', f"Could not resolve the bot's own membership in {guild.name}.")]

        problems = []
        if not bot_member.guild_permissions.manage_roles:
            problems.append(ConfigProblem('error', "Missing the Manage Roles permission, so no roles can be handed out at all."))

        problems.extend(self.check_role(guild, bot_member, GOOLORD_ROLE_ID, 'GOOLORD_ROLE_ID'))
        problems.extend(self.check_role(guild, bot_member, GOO_JAIL_ROLE_ID, 'GOO_JAIL_ROLE_ID'))

        jail_role = guild.get_role(GOO_JAIL_ROLE_ID)
        already_seen = set()
        for channel_id in GOO_CHANNELS:
            if channel_id in already_seen:
                problems.append(ConfigProblem('warning', f"Channel {channel_id} is listed more than once in GOO_CHANNELS."))
                continue
            already_seen.add(channel_id)
            problems.extend(self.check_channel(guild, bot_member, jail_role, channel_id))

        problems.extend(self.check_jail_channel(guild, bot_member, jail_role))
        problems.extend(self.check_general_channel(guild, bot_member))
        return problems

    def report_configuration(self) -> List[ConfigProblem]:
        problems = self.verify_configuration()
        errors = [problem for problem in problems if problem.severity == 'error']
        warnings = [problem for problem in problems if problem.severity == 'warning']

        for problem in errors:
            logger.error(f"goo config: {problem.message}")
        for problem in warnings:
            logger.warning(f"goo config: {problem.message}")

        if not problems:
            logger.info("goo config: guild, channels, roles and permissions all check out")
        else:
            logger.error(f"goo config: {len(errors)} error(s) and {len(warnings)} warning(s), goo may not work correctly")
        return problems

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def goocheck(self, ctx: commands.Context):
        problems = self.report_configuration()
        errors = [problem for problem in problems if problem.severity == 'error']
        warnings = [problem for problem in problems if problem.severity == 'warning']

        if not problems:
            embed = discord.Embed(
                title="Goo configuration is okay",
                description="Guild, channels, roles and permissions all check out.",
                color=0x39e81a,
            )
        else:
            embed = discord.Embed(
                title="Goo configuration has an error",
                description=f"{len(errors)} error(s) and {len(warnings)} warning(s).",
                color=0xe81a1a if errors else 0xe8a71a,
            )
            if errors:
                embed.add_field(name="Broken", value="\n".join(f"• {problem.message}" for problem in errors)[:1024], inline=False)
            if warnings:
                embed.add_field(name="Degraded", value="\n".join(f"• {problem.message}" for problem in warnings)[:1024], inline=False)
        await ctx.send(embed=embed)

    async def check_existing_goolord(self):
        """
        If the db has been reset, remove any existing goo lords!
        """
        await self.bot.wait_until_ready()
        self.report_configuration()
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
            sentence = self.bot.jail_counter.get(member.id)
            if sentence is not None and sentence.release_at > now:
                continue
            if not await self.release_from_jail(jail_role, member):
                continue
            await channel.send(f"<@{member.id}>, you are now free from jail.")

        expired = [user_id for user_id, sentence in self.bot.jail_counter.items() if sentence.release_at <= now]
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

    async def handle_win(self, ctx: commands.Context, pool: asyncpg.Pool, user: User, current_lord: User|None, opening: str|None = None):
        member = ctx.author
        name = member.display_name
        attempts_before_win = user.loss_count
        now = utc_now()

        # the db is written before any role change so a failed role edit cannot
        # leave stats pointing at a lord with no last_win
        await record_win(pool, member.id, name, now)
        await set_goo_lord(pool, member.id, now)

        response = opening if opening is not None else WIN_MESSAGE.format(name, attempts_before_win)

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

    async def send_to_jail(self, guild: discord.Guild, member: discord.Member, duration: int) -> bool:
        jail_role = guild.get_role(GOO_JAIL_ROLE_ID)
        if jail_role is None:
            logger.error(f"Could not find the goo jail role {GOO_JAIL_ROLE_ID}")
            return False

        self.bot.jail_counter[member.id] = JailSentence.begin(utc_now(), duration)
        try:
            await member.add_roles(jail_role)
        except discord.HTTPException:
            logger.exception(f"Failed to add the goo jail role to {member.id}")
            self.bot.jail_counter.pop(member.id, None)
            return False
        return True

    async def release_from_jail(self, jail_role: discord.Role, member: discord.Member) -> bool:
        try:
            await member.remove_roles(jail_role)
        except discord.HTTPException:
            logger.exception(f"Failed to remove the goo jail role from {member.id}")
            return False
        self.bot.jail_counter.pop(member.id, None)
        return True

    async def maybe_send_to_jail(self, ctx: commands.Context):
        roll = random.randint(1, 100)
        if roll > GOO_LONG_COOLDOWN_CHANCE:
            return

        if await self.send_to_jail(ctx.guild, ctx.author, GOO_LONG_COOLDOWN):
            await ctx.reply(f"Your attempt to overthrow the lord has been thwarted. You have been sent to jail for {round(GOO_LONG_COOLDOWN / 60)} minutes!")

    def prisoners_by_time_remaining(self, jail_role: discord.Role, now: datetime.datetime, exclude: Set[int]) -> List[Tuple[discord.Member, JailSentence]]:
        prisoners = []
        for member in jail_role.members:
            if member.id in exclude:
                continue
            sentence = self.bot.jail_counter.get(member.id)
            # anyone with no sentence left is on their way out already
            if sentence is None or sentence.seconds_remaining(now) <= 0:
                continue
            prisoners.append((member, sentence))
        prisoners.sort(key=lambda prisoner: prisoner[1].release_at, reverse=True)
        return prisoners

    def extend_sentences(self, member_ids: List[int], seconds: int):
        for member_id in member_ids:
            sentence = self.bot.jail_counter.get(member_id)
            if sentence is not None:
                self.bot.jail_counter[member_id] = sentence.extended_by(seconds)

    @commands.cooldown(1, JAILBREAK_COOLDOWN, commands.BucketType.user)
    @commands.command()
    @commands.check(is_jail_channel)
    async def jailbreak(self, ctx: commands.Context):
        jail_role = ctx.guild.get_role(GOO_JAIL_ROLE_ID)
        if jail_role is None:
            ctx.command.reset_cooldown(ctx)
            logger.error(f"Could not find the goo jail role {GOO_JAIL_ROLE_ID}")
            await ctx.reply("The goo jail seems to have vanished. Tell an admin.")
            return
        if jail_role not in ctx.author.roles:
            ctx.command.reset_cooldown(ctx)
            await ctx.reply("You are not in goo jail. There is nothing for you to break out of.")
            return

        now = utc_now()
        sentence = self.bot.jail_counter.get(ctx.author.id)
        if sentence is None or sentence.seconds_remaining(now) <= 0:
            ctx.command.reset_cooldown(ctx)
            await ctx.reply("Your sentence is already served. Sit tight, the door opens on its own.")
            return

        chance = jailbreak_chance(sentence, now)
        if random.randint(1, 100) > chance:
            await ctx.reply(JAILBREAK_FAIL_MESSAGE.format(chance, format_countdown(sentence.seconds_remaining(now))))
            return

        if not await self.release_from_jail(jail_role, ctx.author):
            ctx.command.reset_cooldown(ctx)
            await ctx.reply("The lock gives, then holds. Something went wrong, try again.")
            return

        await self.cascade_jailbreak(ctx, jail_role, now)

    async def cascade_jailbreak(self, ctx: commands.Context, jail_role: discord.Role, now: datetime.datetime):
        escapees = [ctx.author]
        lines = [JAILBREAK_OPEN_MESSAGE.format(ctx.author.id)]

        # the angriest prisoner runs first, and the chain dies with the first failure
        followers = self.prisoners_by_time_remaining(jail_role, now, {ctx.author.id})
        broke_at = len(followers)
        for index, (member, sentence) in enumerate(followers):
            remaining = format_countdown(sentence.seconds_remaining(now))
            escaped = random.randint(1, 100) <= JAILBREAK_FOLLOW_CHANCE and await self.release_from_jail(jail_role, member)
            if not escaped:
                lines.append(JAILBREAK_CAUGHT_MESSAGE.format(member.id, remaining))
                broke_at = index
                break
            lines.append(JAILBREAK_FOLLOW_MESSAGE.format(member.id, remaining))
            escapees.append(member)

        left_behind = followers[broke_at:]
        if not followers:
            lines.append(JAILBREAK_EMPTY_MESSAGE)
        elif not left_behind:
            lines.append(JAILBREAK_CLEARED_MESSAGE)
        else:
            self.extend_sentences([member.id for member, _ in left_behind], JAILBREAK_PENALTY)
            lines.append(JAILBREAK_PENALTY_MESSAGE.format(len(left_behind), round(JAILBREAK_PENALTY / 60)))

        await ctx.send("\n".join(lines))
        await self.announce_jailbreak(escapees)

    async def announce_jailbreak(self, escapees: List[discord.Member]):
        channel = self.bot.get_channel(GOO_CHANNELS[0])
        if channel is None:
            return
        names = ", ".join(f"<@{member.id}>" for member in escapees)
        try:
            await channel.send(JAILBREAK_ANNOUNCE_MESSAGE.format(names))
        except discord.HTTPException:
            logger.exception("Failed to announce the jailbreak in the goo channel")

    @commands.command()
    @commands.check(is_goo_channel)
    async def duel(self, ctx: commands.Context):
        pool = self.get_pool()
        if pool is None:
            return

        member = ctx.author
        name = member.display_name
        user = await init_user(pool, member)

        stats = await get_stats(pool)
        if stats is None:
            logger.error("the stats row is missing")
            return

        # nothing below here should be reachable without a lord to fight
        current_lord = await get_goolord(pool, stats)
        if current_lord is None:
            await ctx.reply("There is no goo lord to duel. The throne lies empty, go hop for it.")
            return
        if current_lord.id == member.id:
            await ctx.reply("You are the goo lord. Duelling yourself would settle nothing.")
            return

        now = utc_now()
        earliest_allowed = now - datetime.timedelta(seconds=DUEL_COOLDOWN)
        # the daily is claimed before the roll so a double invocation cannot duel twice
        claimed = await claim_daily_duel(pool, member.id, now, earliest_allowed)
        if claimed is None:
            remaining = DUEL_COOLDOWN - (now - user.last_duel).total_seconds() if user.last_duel else DUEL_COOLDOWN
            await ctx.reply(f"You have already duelled today, {name}. Sharpen your blade and return in {format_countdown(remaining)}.")
            return

        roll = random.randint(1, 100)
        if roll <= DUEL_CHANCE:
            await self.handle_win(ctx, pool, claimed, current_lord, opening=DUEL_WIN_MESSAGE.format(name))
            return

        loser = await record_loss(pool, member.id, name)
        await ctx.send(DUEL_LOSS_MESSAGE.format(name, round(DUEL_LOSS_JAIL / 60)))
        if loser is not None:
            await self.check_biggest_loser(ctx, pool, stats, loser.loss_count)
        await self.send_to_jail(ctx.guild, member, DUEL_LOSS_JAIL)

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

        if not await self.send_to_jail(ctx.guild, member, BANISH_DURATION):
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
        await self.broadcast_decree(ctx, text)

    async def broadcast_decree(self, ctx: commands.Context, text: str):
        if GENERAL_CHANNEL_ID is None:
            return

        channel = ctx.guild.get_channel(GENERAL_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            logger.error(f"GENERAL_CHANNEL_ID {GENERAL_CHANNEL_ID} is not a text channel in this guild")
            return
        if channel.id == ctx.channel.id:
            return

        try:
            await channel.send(DECREE_MESSAGE.format(text))
        except discord.Forbidden:
            await ctx.reply(f"I am not allowed to post in {channel.mention}, so the decree stayed here.")
        except discord.HTTPException:
            logger.exception("Failed to broadcast the decree")

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
