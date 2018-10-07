"""Module for the Cheater cog."""

import asyncio
from copy import copy
import contextlib
import discord

from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.tunnel import Tunnel

_ = Translator("Cheater", __file__)


@cog_i18n(_)
class Cheater:

    default_report = {"report": {}}
    """Cheater cog!"""
    def __init__(self, bot: Red):
        glob = {
            'cheaterchannel': "",
            'cheaterlogacceptedchannel': "",
            'cheaterlogdeniedchannel': "",
            'next_ticket': 0,
            'cheaterusermessages': {}
        }
        self.bot = bot
        self.config = Config.get_conf(self, identifier=494646941, force_registration=True)
        self.config.register_global(**glob)
        self.config.register_custom("CHEATER", **self.default_report)
        self.user_cache = []
        self.tunnel_store = {}


    @checks.is_owner()
    @commands.group(name="cheaterset")
    async def cheaterset(self, ctx: commands.Context):
        """
        Settings for the report system.
        """
        pass

    @checks.is_owner()
    @commands.guild_only()
    @cheaterset.command(name="output")
    async def set_output(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where cheater reports will show up"""
        await self.config.cheaterchannel.set(channel.id)
        await ctx.send(_("The report channel has been set."))

    @checks.is_owner()
    @commands.guild_only()
    @cheaterset.command(name="logaccepted")
    async def set_alog(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where acepted logs will show up"""
        await self.config.cheaterlogacceptedchannel.set(channel.id)
        await ctx.send(_("The report channel has been set."))

    @checks.is_owner()
    @commands.guild_only()
    @cheaterset.command(name="logdenied")
    async def set_dlog(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where denied logs will show up"""
        await self.config.cheaterlogdeniedchannel.set(channel.id)
        await ctx.send(_("The report channel has been set."))

    @commands.group(name="cheater", invoke_without_command=True)
    async def cheater(self, ctx: commands.Context, *, _report: str = ""):
        """
        Send a cheater report.

        Use without arguments for interactive reporting, or do
        [p]report <text> to use it non-interactively.
        """
        author = ctx.author
        if author.id in self.user_cache:
            return await author.send(
                _(
                    "Please finish making your prior report before trying to make an "
                    "additional one!"
                )
            )
        self.user_cache.append(author.id)

        if _report:
            _m = copy(ctx.message)
            _m.content = _report
            _m.content = _m.clean_content
            val = await self.send_report(_m)
        else:
            try:
                dm = await author.send(
                    _(
                        "Please respond to this message with your Report."
                        "\nYour report should be a single message"
                    )
                )
            except discord.Forbidden:
                return await ctx.send(_("This requires DMs enabled."))

            def pred(m):
                return m.author == author and m.channel == dm.channel

            try:
                message = await self.bot.wait_for("message", check=pred, timeout=180)
            except asyncio.TimeoutError:
                return await author.send(_("You took too long. Try again later."))
            else:
                val = await self.send_report(message)

        with contextlib.suppress(discord.Forbidden, discord.HTTPException):
            if val is None:
                await author.send(
                    _("There was an error sending your report, please contact a server admin.")
                )
            else:
                await author.send(_("Your report was submitted. (Ticket #{})").format(val))

    @cheater.after_invoke
    async def report_cleanup(self, ctx: commands.Context):
        """
        The logic is cleaner this way
        """
        if ctx.author.id in self.user_cache:
            self.user_cache.remove(ctx.author.id)
        if ctx.guild and ctx.invoked_subcommand is None:
            if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
                try:
                    await ctx.message.delete()
                except discord.NotFound:
                    pass

    async def on_message(self, message: discord.Message):
        for k, v in self.tunnel_store.items():
            topic = _("Re: ticket# {1} in {0.name}").format(*k)
            # Tunnels won't forward unintended messages, this is safe
            msgs = await v["tun"].communicate(message=message, topic=topic)
            if msgs:
                self.tunnel_store[k]["msgs"] = msgs

    @commands.guild_only()
    @checks.mod_or_permissions(manage_members=True)
    @cheater.command(name="interact")
    async def response(self, ctx, ticket_number: int):
        """
        Open a message tunnel.

        This tunnel will forward things you say in this channel
        to the ticket opener's direct messages.

        Tunnels do not persist across bot restarts.
        """

        guild = ctx.guild
        rec = await self.config.custom("CHEATER", ticket_number).report()

        try:
            user = guild.get_member(rec.get("user_id"))
        except KeyError:
            return await ctx.send(_("That ticket doesn't seem to exist"))

        if user is None:
            return await ctx.send(_("That user isn't here anymore."))

        tun = Tunnel(recipient=user, origin=ctx.channel, sender=ctx.author)

        if tun is None:
            return await ctx.send(
                _(
                    "Either you or the user you are trying to reach already "
                    "has an open communication."
                )
            )

        big_topic = _(
            "{who} opened a 2-way communication "
            "about ticket number {ticketnum}. Anything you say or upload here "
            "(8MB file size limitation on uploads) "
            "will be forwarded to them until the communication is closed.\n"
            "You can close a communication at any point by reacting with "
            "the \N{NEGATIVE SQUARED CROSS MARK} to the last message recieved.\n"
            "Any message succesfully forwarded will be marked with "
            "\N{WHITE HEAVY CHECK MARK}.\n"
            "Tunnels are not persistent across bot restarts."
        )
        topic = big_topic.format(
            ticketnum=ticket_number, who=_("A moderator in `{guild.name}` has").format(guild=guild)
        )
        try:
            m = await tun.communicate(message=ctx.message, topic=topic, skip_message_content=True)
        except discord.Forbidden:
            await ctx.send(_("That user has DMs disabled."))
        else:
            self.tunnel_store[(guild, ticket_number)] = {"tun": tun, "msgs": m}
            await ctx.send(big_topic.format(who=_("You have"), ticketnum=ticket_number))

    async def send_report(self, msg: discord.Message):

        async def _search_menu(
            ctx: commands.Context,
            pages: list,
            controls: dict,
            message: discord.Message,
            page: int,
            timeout: float,
            emoji: str,
        ):
            if message:
                await self._search_button_action(ctx, tracks, emoji, page)
                await message.delete()
                return None

        SEARCH_CONTROLS = {
            "⬅": high,
            "❌": medium,
            "➡": low
        }

        author = msg.author
        report = msg.clean_content
        channel = self.bot.get_channel(await self.config.cheaterchannel())
        files = await Tunnel.files_from_attatch(msg)
        ticket_number = await self.config.next_ticket()
        await self.config.next_ticket.set(ticket_number + 1)

        try:
            em = discord.Embed(description=report)
            em.set_author(
                name=_("Report from {author}").format(
                    author=author.name),
                icon_url=author.avatar_url)
            em.set_footer(text=_("Report #{}").format(ticket_number))
            send_content = None
        except (discord.Forbidden, discord.HTTPException):
            em = None
            send_content = _("Report from {author.mention} (Ticket #{number})").format(
                author=author.id, number=ticket_number
            )
            send_content += "\n" + report

        try:
            rets = await Tunnel.message_forwarder(
                destination=channel, content=send_content, embed=em, files=files
            )
            for x in rets:
                await x.add_reaction("\N{WHITE HEAVY CHECK MARK}")
                await x.add_reaction("\N{HEAVY LARGE CIRCLE}")
                await x.add_reaction("\N{CROSS MARK}")
                async with self.config.cheaterusermessages() as cum:
                    cum.update({x.id: author.id})
        except (discord.Forbidden, discord.HTTPException):
            return None

        await self.config.custom("CHEATER", ticket_number).report.set(
            {"user_id": author.id, "report": report}
        )
        return ticket_number

    @property
    def tunnels(self):
        return [x["tun"] for x in self.tunnel_store.values()]

    async def on_reaction_add(self, reaction):
        """
        oh dear....
        """
        mess = reaction.message.id
        logcchannel = await self.config.cheaterchannel()
        dicti = await self.config.cheaterusermessages()
        logachannel = await self.config.cheaterlogacceptedchannel()
        logdchannel = await self.config.cheaterlogdeniedchannel()
        origauthor = self.bot.get_user(int(dicti[_id]))
        await logcchannel.send(str(mess))

        if _id in dicti.keys():
            if str(payload.emoji) == "\N{WHITE HEAVY CHECK MARK}":
                await logachannel.send("Report complete and marked as cheater banned")
                await origauthor.send("Your report has been marked as closed - the cheating person has been banned.")
                if message.content is not None:
                    await logachannel.send(message.content)
                    await origauthor.send(message.content)
                for embed in message.embeds:
                    await logachannel.send(embed=embed)
                await message.delete()
                await dicti.pop(message.id)

            elif str(payload.emoji) == "\N{HEAVY LARGE CIRCLE}":
                await logdchannel.send("Report complete and marked as info incomplete or other investigation issue")
                await origauthor.send("Your report has been marked as closed - incomplete"
                                      " info or investigation issues.")
                if message.content is not None:
                    await logdchannel.send(message.content)
                    await origauthor.send(message.content)
                for embed in message.embeds:
                    await logdchannel.send(embed=embed)
                await message.delete()
                await dicti.pop(message.id)

            elif str(payload.emoji) == "\N{CROSS MARK}":
                await logdchannel.send("Report complete and marked as unable to confirm the user is cheating")
                await origauthor.send("Your report has been marked as closed - unable to confirm the user is cheating.")
                if message.content is not None:
                    await logdchannel.send(message.content)
                    await origauthor.send(message.content)
                for embed in message.embeds:
                    await logdchannel.send(embed=embed)
                await message.delete()
                await dicti.pop(message.id)

    async def on_raw_reaction_add(self, payload):
        """
        oh dear....
        """
        if not str(payload.emoji) == "\N{NEGATIVE SQUARED CROSS MARK}":
            return

        _id = payload.message_id
        t = next(filter(lambda x: _id in x[1]["msgs"], self.tunnel_store.items()), None)

        if t is None:
            return
        tun = t[1]["tun"]
        if payload.user_id in [x.id for x in tun.members]:
            await tun.react_close(
                uid=payload.user_id, message=_("{closer} has closed the correspondence")
            )
            self.tunnel_store.pop(t[0], None)