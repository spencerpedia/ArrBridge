from __future__ import annotations

import aiohttp


class SonarrError(Exception):
    pass


class SonarrClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}

    async def _get(self, session: aiohttp.ClientSession, path: str, **params):
        url = f"{self._base_url}{path}"
        async with session.get(url, headers=self._headers, params=params) as resp:
            if resp.status != 200:
                raise SonarrError(f"GET {path} failed ({resp.status}): {await resp.text()}")
            return await resp.json()

    async def _post(self, session: aiohttp.ClientSession, path: str, json):
        url = f"{self._base_url}{path}"
        async with session.post(url, headers=self._headers, json=json) as resp:
            if resp.status not in (200, 201):
                raise SonarrError(f"POST {path} failed ({resp.status}): {await resp.text()}")
            return await resp.json()

    async def lookup_series(self, session: aiohttp.ClientSession, term: str):
        return await self._get(session, "/api/v3/series/lookup", term=term)

    async def get_root_folders(self, session: aiohttp.ClientSession):
        return await self._get(session, "/api/v3/rootfolder")

    async def get_quality_profiles(self, session: aiohttp.ClientSession):
        return await self._get(session, "/api/v3/qualityprofile")

    async def get_existing_series(self, session: aiohttp.ClientSession):
        return await self._get(session, "/api/v3/series")

    async def add_series(self, session: aiohttp.ClientSession, series: dict):
        return await self._post(session, "/api/v3/series", series)
