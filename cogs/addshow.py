from __future__ import annotations

import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from sonarr_client import SonarrClient, SonarrError

SONARR_URL = os.environ["SONARR_URL"]
SONARR_API_KEY = os.environ["SONARR_API_KEY"]

MAX_RESULTS = 25


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
            "addOptions": {"searchForMissingEpisodes": True, "monitor": "all"},
        }

        async with aiohttp.ClientSession() as session:
            try:
                await self.client.add_series(session, payload)
            except SonarrError as exc:
                await interaction.response.edit_message(content=f"Failed to add series: {exc}", view=None)
                return

        await interaction.response.edit_message(
            content=f"Added **{self.show['title']}** to Sonarr and started searching for episodes.",
            view=None,
        )


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
                if any(s.get("tvdbId") == show.get("tvdbId") for s in existing):
                    await interaction.response.edit_message(
                        content=f"**{show['title']}** is already in Sonarr.", view=None
                    )
                    return

                root_folders = await self.client.get_root_folders(session)
                quality_profiles = await self.client.get_quality_profiles(session)
            except SonarrError as exc:
                await interaction.response.edit_message(content=f"Sonarr error: {exc}", view=None)
                return

        if not root_folders:
            await interaction.response.edit_message(content="No root folders configured in Sonarr.", view=None)
            return
        if not quality_profiles:
            await interaction.response.edit_message(content="No quality profiles configured in Sonarr.", view=None)
            return

        view = AddOptionsView(self.client, show, root_folders, quality_profiles, self.invoker_id)
        await interaction.response.edit_message(
            content=f"Adding **{show['title']}** ({show.get('year') or '????'}) "
                    f"— choose root folder and quality profile:",
            view=view,
        )


class AddShow(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = SonarrClient(SONARR_URL, SONARR_API_KEY)

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


async def setup(bot: commands.Bot):
    await bot.add_cog(AddShow(bot))
