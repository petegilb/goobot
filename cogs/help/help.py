import logging
from typing import TYPE_CHECKING, Dict, List, NamedTuple

import discord
from discord.ext import commands

from cogs.goo.goo import (
    GOO_CHANCE, GOO_COOLDOWN, GOO_LONG_COOLDOWN, GOO_LONG_COOLDOWN_CHANCE,
    DUEL_CHANCE, DUEL_COOLDOWN, DUEL_LOSS_JAIL,
    BANISH_DURATION, DECREE_COOLDOWN, MAX_DECREE_LENGTH,
)

if TYPE_CHECKING:
    from main import GooBot

logger = logging.getLogger('discord')

GOO_COLOR = 0x39e81a


def describe_duration(seconds: int) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    minutes = max(seconds // 60, 1)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


class HelpEntry(NamedTuple):
    usage: str
    summary: str
    detail: str
    group: str


THRONE = "Taking the throne"
LORD = "Goo lord only"
LOOKUP = "Looking things up"
OTHER = "Everything else"
ADMIN = "Admin"

GROUP_ORDER = [THRONE, LORD, LOOKUP, OTHER, ADMIN]

HELP_ENTRIES: Dict[str, HelpEntry] = {
    'hopinmygoo': HelpEntry(
        usage="hopinmygoo",
        summary=f"Attempt to usurp the crown. {GOO_CHANCE}% chance, once every {describe_duration(GOO_COOLDOWN)}.",
        detail=(
            f"Each attempt has a **{GOO_CHANCE}% chance** of making you the goo lord, "
            f"and you can try once every **{describe_duration(GOO_COOLDOWN)}**.\n\n"
            f"Every failure adds to your loss streak, which is tracked forever even after you finally win. "
            f"There is also a **{GOO_LONG_COOLDOWN_CHANCE}% chance** on any failure that you are thrown into "
            f"goo jail for **{describe_duration(GOO_LONG_COOLDOWN)}**, which locks you out of the goo channel entirely.\n\n"
            "You cannot overthrow yourself, so the reigning lord has to sit and wait like everyone else."
        ),
        group=THRONE,
    ),
    'duel': HelpEntry(
        usage="duel",
        summary=f"Challenge the lord to a duel. {DUEL_CHANCE}% to win, once every {describe_duration(DUEL_COOLDOWN)}.",
        detail=(
            f"A direct challenge to the sitting goo lord, usable **once every {describe_duration(DUEL_COOLDOWN)}**.\n\n"
            f"**Win ({DUEL_CHANCE}% chance):** you take the throne immediately, no begging required.\n"
            f"**Lose:** the lord holds firm and you are thrown into goo jail for "
            f"**{describe_duration(DUEL_LOSS_JAIL)}**.\n\n"
        ),
        group=THRONE,
    ),
    'banish': HelpEntry(
        usage="banish @user",
        summary=f"Throw someone in the goo dungeon for {describe_duration(BANISH_DURATION)}. Once per reign.",
        detail=(
            f"The goo lord's privilege. Sends the target to goo jail for **{describe_duration(BANISH_DURATION)}**, "
            "locking them out of the goo channel so they cannot come for your crown.\n\n"
            "**Once per reign.** Lose the throne and win it back, and you get a fresh one. "
        ),
        group=LORD,
    ),
    'decree': HelpEntry(
        usage="decree <your proclamation>",
        summary="Set the bot status and the goo channel topic.",
        detail=(
            "Speak as the goo lord. Your words become the bot's status **and** the topic of the goo channel, "
            "so everyone sees them whether they are reading the chat or not.\n\n"
            f"Up to **{MAX_DECREE_LENGTH} characters**, and usable once every "
            f"**{describe_duration(DECREE_COOLDOWN)}** because Discord rate limits topic changes hard.\n\n"
        ),
        group=LORD,
    ),
    'goolord': HelpEntry(
        usage="goolord",
        summary="Who currently holds the throne, and for how long.",
        detail="Shows the reigning goo lord and how long their current reign has lasted.",
        group=LOOKUP,
    ),
    'gooreign': HelpEntry(
        usage="gooreign [user]",
        summary="Total time somebody has ruled, across all their reigns.",
        detail=(
            "Shows how many times somebody has been goo lord and their total time on the throne.\n\n"
            "Defaults to you. Pass a mention or a username to look somebody else up. "
            "If the target is the current lord, their ongoing reign is included in the total."
        ),
        group=LOOKUP,
    ),
    'gooleaderboard': HelpEntry(
        usage="gooleaderboard",
        summary="The full standings, ranked by time on the throne.",
        detail=(
            "The full table, ranked by total time as goo lord. Columns are time (minutes), wins, "
            "current loss streak, total hops and longest ever loss streak.\n\n"
            "The current goo lord is highlighted in green and the biggest loser in red. "
            "People who have left the server are left out."
        ),
        group=LOOKUP,
    ),
    'gooloser': HelpEntry(
        usage="gooloser",
        summary="Who holds the record for most failures without a win.",
        detail=(
            "The hall of shame. Shows who holds the record for the most consecutive failed attempts "
            "at the throne, and how many it took. Beat their streak and the title becomes yours, "
            "announced to the whole channel."
        ),
        group=LOOKUP,
    ),
    'goostats': HelpEntry(
        usage="goostats",
        summary="Raw global stats for the kingdom.",
        detail="Dumps the raw global stats record: current lord, biggest loser, and when things last changed.",
        group=LOOKUP,
    ),
    'gooball': HelpEntry(
        usage="gooball",
        summary="GOOOOOOB.",
        detail="Posts the gooball. That is the entire feature.",
        group=OTHER,
    ),
    'hello': HelpEntry(
        usage="hello [user]",
        summary="The bot says hello back.",
        detail="Says hello. Greets you by default, or somebody else if you name them.",
        group=OTHER,
    ),
    'minecraft': HelpEntry(
        usage="minecraft",
        summary="Status of the Minecraft server.",
        detail=(
            "Checks the configured Minecraft server and reports whether it is online, which version "
            "it runs, and who is currently playing."
        ),
        group=OTHER,
    ),
    'ping': HelpEntry(
        usage="ping",
        summary="Check the bot is alive.",
        detail="Replies with the bot's latency. Also available as a slash command.",
        group=OTHER,
    ),
}


class Help(commands.Cog):

    def __init__(self, bot):
        self.bot: GooBot = bot

    def visible_groups(self, ctx: commands.Context) -> List[str]:
        permissions = getattr(ctx.author, 'guild_permissions', None)
        if permissions is not None and permissions.administrator:
            return GROUP_ORDER
        return [group for group in GROUP_ORDER if group != ADMIN]

    def game_summary(self, prefix: str) -> str:
        return (
            "Let us fight for the glory of the throne of goo.\n\n"
            f"`{prefix}hopinmygoo` is your repeatable roll at the crown. `{prefix}duel` is a much better "
            "shot but only once a day, and losing it puts you behind bars. Whoever is wearing the crown "
            f"can `{prefix}banish` a rival and `{prefix}decree` whatever they like.\n\n"
            "The leaderboard ranks **total time on the throne**, not wins."
        )

    @commands.command()
    async def help(self, ctx: commands.Context, *, command_name: str = None):
        if command_name:
            await self.send_command_help(ctx, command_name.strip().lstrip('!'))
            return
        await self.send_overview(ctx)

    async def send_overview(self, ctx: commands.Context):
        prefix = ctx.prefix or '!'
        embed = discord.Embed(
            title="🐌 GooBot Help",
            description=f"{self.game_summary(prefix)}\n\nUse `{prefix}help <command>` for the detail on any of these.",
            color=GOO_COLOR,
        )

        for group in self.visible_groups(ctx):
            lines = [
                f"`{prefix}{entry.usage}`\n{entry.summary}"
                for entry in HELP_ENTRIES.values()
                if entry.group == group
            ]
            if lines:
                embed.add_field(name=group, value="\n\n".join(lines), inline=False)

        embed.set_footer(text="The goo commands only work in the goo channels.")
        await ctx.send(embed=embed)

    async def send_command_help(self, ctx: commands.Context, command_name: str):
        prefix = ctx.prefix or '!'
        entry = HELP_ENTRIES.get(command_name.lower())
        if entry is None or entry.group not in self.visible_groups(ctx):
            await ctx.reply(f"No such command. Try `{prefix}help` for the full list.")
            return

        embed = discord.Embed(
            title=f"{prefix}{entry.usage}",
            description=entry.detail,
            color=GOO_COLOR,
        )
        embed.set_footer(text=f"{entry.group}  ·  {prefix}help for everything else")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
