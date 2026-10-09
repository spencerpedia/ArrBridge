from __future__ import annotations

import asyncio
import logging
import os
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import pending_store
from sonarr_client import SonarrClient, SonarrError

log = logging.getLogger("arrbridge")

PENDING_KIND = "series"

SONARR_URL = os.environ["SONARR_URL"]
SONARR_API_KEY = os.environ["SONARR_API_KEY"]

MAX_RESULTS = 25
POLL_INTERVAL_SECONDS = 15
SEARCH_TIMEOUT_SECONDS = 5 * 60
DOWNLOAD_TIMEOUT_SECONDS = 45 * 60

FINISHED_COMMAND_STATUSES = ("completed", "failed", "aborted", "cancelled")
UNSUCCESSFUL_COMMAND_STATUSES = ("failed", "aborted", "cancelled")
# Sonarr forgets command IDs when it restarts, so polling one can fail forever.
MAX_CONSECUTIVE_FAILURES = 20

# asyncio.create_task() doesn't keep a strong reference to the task, so without
# this it can be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


def _truncate(text: str, length: int = 280) -> str:
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0] + "..."


def build_show_embed(show: dict, status: str | None = None) -> discord.Embed:
    poster = next(
        (img["remoteUrl"] for img in show.get("images", []) if img.get("coverType") == "poster" and img.get("remoteUrl")),
        None,
    )
    embed = discord.Embed(
        title=f"{show['title']} ({show.get('year') or '????'})",
        description=_truncate(show.get("overview") or ""),
        color=discord.Color.blue(),
    )
    if poster:
        embed.set_thumbnail(url=poster)
    if show.get("network"):
        embed.add_field(name="Network", value=show["network"])
    if show.get("status"):
        embed.add_field(name="Status", value=show["status"].title())
    genres = show.get("genres") or []
    if genres:
        embed.add_field(name="Genres", value=", ".join(genres[:5]))
    if status:
        embed.set_footer(text=status)
    return embed


async def _safe_edit(message: discord.Message, embed: discord.Embed):
    try:
        await message.edit(embed=embed)
    except discord.HTTPException:
        log.warning("Could not update status message %s", message.id, exc_info=True)


async def _set_status(message: discord.Message, embed: discord.Embed, text: str):
    embed.set_footer(text=text)
    await _safe_edit(message, embed)


def _queue_progress_text(queue: list[dict]) -> str:
    total_size = sum(item.get("size", 0) for item in queue)
    total_left = sum(item.get("sizeleft", 0) for item in queue)
    pct = round((total_size - total_left) / total_size * 100) if total_size else 0
    seasons = sorted({
        item["episode"]["seasonNumber"] for item in queue if item.get("episode")
    })
    unit = f"{len(seasons)} season(s)" if seasons else f"{len(queue)} episode(s)"
    return f"Downloading {unit} - {pct}%"


async def get_series_status_text(client: SonarrClient, session: aiohttp.ClientSession, series: dict) -> str:
    try:
        queue = await client.get_queue(session, series["id"])
    except SonarrError:
        queue = []

    if queue:
        return _queue_progress_text(queue)

    stats = series.get("statistics") or {}
    monitored = "Monitored" if series.get("monitored") else "Not monitored"
    episode_count = stats.get("episodeCount", 0)
    if episode_count:
        file_count = stats.get("episodeFileCount", 0)
        return f"{monitored} - {file_count}/{episode_count} episodes downloaded"
    return f"{monitored} - no episodes tracked yet"


async def _await_search_command(client: SonarrClient, session: aiohttp.ClientSession, command_id: int) -> str | None:
    """Poll a Sonarr command until it finishes.

    Returns the final status, or None if it could not be determined within the
    timeout. Every exit path is bounded by the deadline - a command we can no
    longer read is not worth polling forever, and the download it kicked off is
    still worth tracking in the queue.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SEARCH_TIMEOUT_SECONDS
    failures = 0

    while loop.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            command = await client.get_command(session, command_id)
        except SonarrError:
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                log.warning("Gave up polling Sonarr command %s after %d failures", command_id, failures)
                return None
            continue

        failures = 0
        status = command.get("status")
        if status in FINISHED_COMMAND_STATUSES:
            return status

    log.warning("Sonarr command %s did not finish within %ds", command_id, SEARCH_TIMEOUT_SECONDS)
    return None


def _spawn(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_tracker(message: discord.Message, embed: discord.Embed, tracker):
    """Run a tracking coroutine, dropping its pending entry once it's finished."""
    try:
        await tracker
    except asyncio.CancelledError:
        # The bot is shutting down - keep the entry so the next start resumes it.
        raise
    except Exception:
        log.exception("Download tracking for message %s crashed", message.id)
        await _set_status(message, embed, "Tracking stopped unexpectedly - check Sonarr for status")
    pending_store.remove(PENDING_KIND, message.id)


async def track_download(client: SonarrClient, message: discord.Message, show: dict, embed: discord.Embed):
    pending_store.add(PENDING_KIND, message.id, message.channel.id, show["id"], embed.footer.text)
    await _run_tracker(message, embed, _search_and_track(client, message, show["id"], embed))


async def resume_download(client: SonarrClient, message: discord.Message, embed: discord.Embed, entry: dict):
    # No deadline means the restart hit during the search phase; Sonarr carries
    # on with the search regardless, so just start watching the queue afresh.
    # Either way, check at least once so a finished download still shows as done.
    deadline = entry.get("deadline") or time.time() + DOWNLOAD_TIMEOUT_SECONDS
    deadline = max(deadline, time.time() + POLL_INTERVAL_SECONDS)
    tracker = _track_queue(
        client, message, entry["item_id"], embed, deadline,
        ever_queued=entry.get("ever_queued", False), last_status=entry.get("status"),
    )
    await _run_tracker(message, embed, tracker)


async def _search_and_track(client: SonarrClient, message: discord.Message, series_id: int, embed: discord.Embed):
    async with aiohttp.ClientSession() as session:
        try:
            command = await client.trigger_series_search(session, series_id)
        except SonarrError:
            log.exception("Could not start a Sonarr search for series %s", series_id)
            await _set_status(message, embed, "Failed to start search - try searching from Sonarr directly")
            return

        status = await _await_search_command(client, session, command["id"])
        if status in UNSUCCESSFUL_COMMAND_STATUSES:
            await _set_status(message, embed, "Search did not complete - check Sonarr for details")
            return

    # Wall-clock rather than loop.time() so the deadline survives a restart.
    deadline = time.time() + DOWNLOAD_TIMEOUT_SECONDS
    pending_store.update(PENDING_KIND, message.id, deadline=deadline)
    await _track_queue(client, message, series_id, embed, deadline)


async def _track_queue(client: SonarrClient, message: discord.Message, series_id: int, embed: discord.Embed,
                       deadline: float, ever_queued: bool = False, last_status: str | None = None):
    async with aiohttp.ClientSession() as session:
        failures = 0

        while time.time() < deadline:
            try:
                queue = await client.get_queue(session, series_id)
            except SonarrError:
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    log.warning("Lost contact with Sonarr while tracking series %s", series_id)
                    await _set_status(message, embed, "Lost contact with Sonarr - check Sonarr for status")
                    return
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            failures = 0
            if queue:
                ever_queued = True
                status_text = _queue_progress_text(queue)
            else:
                try:
                    episodes = await client.get_episodes(session, series_id)
                except SonarrError:
                    episodes = []
                if any(ep.get("hasFile") for ep in episodes):
                    await _set_status(message, embed, "Download completed")
                    return
                status_text = (
                    "Waiting for a release to be grabbed..." if not ever_queued
                    else "Download removed from queue - check Sonarr"
                )

            if status_text != last_status:
                await _set_status(message, embed, status_text)
                last_status = status_text
                pending_store.update(PENDING_KIND, message.id, status=status_text, ever_queued=ever_queued)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        await _set_status(
            message, embed,
            "Still downloading - check Sonarr for live status" if ever_queued
            else "No release grabbed within the timeout - check Sonarr for status",
        )


class AddOptionsView(discord.ui.View):
    def __init__(self, client: SonarrClient, show: dict, root_folders: list[dict],
                 quality_profiles: list[dict], invoker_id: int):
        super().__init__(timeout=120)
        self.client = client
        self.show = show
        self.invoker_id = invoker_id
        self.root_folder_path = root_folders[0]["path"]
        self.quality_profile_id = quality_profiles[0]["id"]

        root_select = discord.ui.Select(
            placeholder="Root folder",
            options=[
                discord.SelectOption(label=rf["path"][:100], value=rf["path"], default=i == 0)
                for i, rf in enumerate(root_folders)
            ],
        )
        root_select.callback = self.on_root_select
        self.add_item(root_select)

        quality_select = discord.ui.Select(
            placeholder="Quality profile",
            options=[
                discord.SelectOption(label=qp["name"][:100], value=str(qp["id"]), default=i == 0)
                for i, qp in enumerate(quality_profiles)
            ],
        )
        quality_select.callback = self.on_quality_select
        self.add_item(quality_select)

        confirm = discord.ui.Button(label="Add to Sonarr", style=discord.ButtonStyle.success)
        confirm.callback = self.on_confirm
        self.add_item(confirm)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This isn't your prompt to interact with.", ephemeral=True)
            return False
        return True

    async def on_root_select(self, interaction: discord.Interaction):
        self.root_folder_path = interaction.data["values"][0]
        await interaction.response.defer()

    async def on_quality_select(self, interaction: discord.Interaction):
        self.quality_profile_id = int(interaction.data["values"][0])
        await interaction.response.defer()

    async def on_confirm(self, interaction: discord.Interaction):
        payload = {
            "title": self.show["title"],
            "tvdbId": self.show["tvdbId"],
            "titleSlug": self.show["titleSlug"],
            "images": self.show.get("images", []),
            "seasons": self.show.get("seasons", []),
            "qualityProfileId": self.quality_profile_id,
            "rootFolderPath": self.root_folder_path,
            "monitored": True,
            "seasonFolder": True,
            "addOptions": {"searchForMissingEpisodes": False, "monitor": "all"},
        }

        async with aiohttp.ClientSession() as session:
            try:
                created = await self.client.add_series(session, payload)
            except SonarrError as exc:
                await interaction.response.edit_message(content=f"Failed to add series: {exc}", embed=None, view=None)
                return

        embed = build_show_embed(created, status="Searching for episodes...")
        await interaction.response.edit_message(content=None, embed=embed, view=None)

        _spawn(track_download(self.client, interaction.message, created, embed))


class ShowPicker(discord.ui.View):
    def __init__(self, client: SonarrClient, results: list[dict], invoker_id: int):
        super().__init__(timeout=120)
        self.client = client
        self.results = results
        self.invoker_id = invoker_id

        options = []
        for i, show in enumerate(results[:MAX_RESULTS]):
            year = show.get("year") or "????"
            label = f"{show['title']} ({year})"[:100]
            description = (show.get("network") or "")[:100]
            options.append(discord.SelectOption(label=label, value=str(i), description=description))

        select = discord.ui.Select(placeholder="Select a show...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This isn't your prompt to interact with.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        show = self.results[int(interaction.data["values"][0])]

        async with aiohttp.ClientSession() as session:
            try:
                existing = await self.client.get_existing_series(session)
                match = next((s for s in existing if s.get("tvdbId") == show.get("tvdbId")), None)
                if match:
                    status = "Already in Sonarr - " + await get_series_status_text(self.client, session, match)
                    embed = build_show_embed(match, status=status)
                    await interaction.response.edit_message(content=None, embed=embed, view=None)
                    return

                root_folders = await self.client.get_root_folders(session)
                quality_profiles = await self.client.get_quality_profiles(session)
            except SonarrError as exc:
                await interaction.response.edit_message(content=f"Sonarr error: {exc}", embed=None, view=None)
                return

        if not root_folders:
            await interaction.response.edit_message(content="No root folders configured in Sonarr.", view=None)
            return
        if not quality_profiles:
            await interaction.response.edit_message(content="No quality profiles configured in Sonarr.", view=None)
            return

        view = AddOptionsView(self.client, show, root_folders, quality_profiles, self.invoker_id)
        embed = build_show_embed(show, status="Choose a root folder and quality profile below.")
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class StatusPicker(discord.ui.View):
    def __init__(self, client: SonarrClient, results: list[dict], invoker_id: int):
        super().__init__(timeout=120)
        self.client = client
        self.results = results
        self.invoker_id = invoker_id

        options = []
        for i, show in enumerate(results[:MAX_RESULTS]):
            year = show.get("year") or "????"
            label = f"{show['title']} ({year})"[:100]
            options.append(discord.SelectOption(label=label, value=str(i)))

        select = discord.ui.Select(placeholder="Select a show...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This isn't your prompt to interact with.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        show = self.results[int(interaction.data["values"][0])]

        async with aiohttp.ClientSession() as session:
            status = await get_series_status_text(self.client, session, show)

        embed = build_show_embed(show, status=status)
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class AddShow(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = SonarrClient(SONARR_URL, SONARR_API_KEY)
        self._resumed = False

    @commands.Cog.listener()
    async def on_ready(self):
        # on_ready fires again after every gateway reconnect; only resume once.
        if self._resumed:
            return
        self._resumed = True
        for entry in pending_store.pending(PENDING_KIND):
            await self._resume(entry)

    async def _resume(self, entry: dict):
        message_id = entry["message_id"]
        try:
            channel = self.bot.get_channel(entry["channel_id"]) or await self.bot.fetch_channel(entry["channel_id"])
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            log.info("Dropping pending tracker for message %s - it was deleted or is unreachable", message_id)
            pending_store.remove(PENDING_KIND, message_id)
            return
        except (discord.HTTPException, aiohttp.ClientError):
            log.warning("Could not fetch message %s to resume tracking - will retry next start", message_id, exc_info=True)
            return

        if not message.embeds:
            pending_store.remove(PENDING_KIND, message_id)
            return

        log.info("Resuming download tracking for series %s (message %s)", entry["item_id"], message_id)
        _spawn(resume_download(self.client, message, message.embeds[0], entry))

    @app_commands.command(name="addshow", description="Search Sonarr and add a TV show to download.")
    @app_commands.describe(query="Show title to search for")
    async def addshow(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)

        async with aiohttp.ClientSession() as session:
            try:
                results = await self.client.lookup_series(session, query)
            except SonarrError as exc:
                await interaction.followup.send(f"Sonarr error: {exc}")
                return

        if not results:
            await interaction.followup.send(f"No results found for **{query}**.")
            return

        view = ShowPicker(self.client, results, interaction.user.id)
        await interaction.followup.send(f"Found {len(results)} result(s) for **{query}**:", view=view)

    @app_commands.command(name="tvstatus", description="Check the download/library status of a show already in Sonarr.")
    @app_commands.describe(query="Show title to check")
    async def tvstatus(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)

        async with aiohttp.ClientSession() as session:
            try:
                existing = await self.client.get_existing_series(session)
            except SonarrError as exc:
                await interaction.followup.send(f"Sonarr error: {exc}")
                return

        query_lower = query.lower()
        matches = [s for s in existing if query_lower in s.get("title", "").lower()]

        if not matches:
            await interaction.followup.send(f"**{query}** isn't in your Sonarr library.")
            return

        if len(matches) == 1:
            async with aiohttp.ClientSession() as session:
                status_text = await get_series_status_text(self.client, session, matches[0])
            embed = build_show_embed(matches[0], status=status_text)
            await interaction.followup.send(embed=embed)
            return

        view = StatusPicker(self.client, matches, interaction.user.id)
        await interaction.followup.send(f"Found {len(matches)} match(es) for **{query}**:", view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(AddShow(bot))
