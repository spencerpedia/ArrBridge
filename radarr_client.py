from __future__ import annotations

import asyncio

import aiohttp


class RadarrError(Exception):
    pass


# Network failures (DNS blips, refused connections, timeouts) are surfaced as
# RadarrError too, so callers retry them like any other API error instead of the
# exception escaping and killing whatever task made the call.
NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)


class RadarrClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}

    async def _get(self, session: aiohttp.ClientSession, path: str, **params):
        url = f"{self._base_url}{path}"
        try:
            async with session.get(url, headers=self._headers, params=params) as resp:
                if resp.status != 200:
                    raise RadarrError(f"GET {path} failed ({resp.status}): {await resp.text()}")
                return await resp.json()
        except NETWORK_ERRORS as exc:
            raise RadarrError(f"GET {path} failed: {exc!r}") from exc

    async def _post(self, session: aiohttp.ClientSession, path: str, json):
        url = f"{self._base_url}{path}"
        try:
            async with session.post(url, headers=self._headers, json=json) as resp:
                if resp.status not in (200, 201):
                    raise RadarrError(f"POST {path} failed ({resp.status}): {await resp.text()}")
                return await resp.json()
        except NETWORK_ERRORS as exc:
            raise RadarrError(f"POST {path} failed: {exc!r}") from exc

    async def lookup_movie(self, session: aiohttp.ClientSession, term: str):
        return await self._get(session, "/api/v3/movie/lookup", term=term)

    async def get_root_folders(self, session: aiohttp.ClientSession):
        return await self._get(session, "/api/v3/rootfolder")

    async def get_quality_profiles(self, session: aiohttp.ClientSession):
        return await self._get(session, "/api/v3/qualityprofile")

    async def get_existing_movies(self, session: aiohttp.ClientSession):
        return await self._get(session, "/api/v3/movie")

    async def get_movie(self, session: aiohttp.ClientSession, movie_id: int):
        return await self._get(session, f"/api/v3/movie/{movie_id}")

    async def add_movie(self, session: aiohttp.ClientSession, movie: dict):
        return await self._post(session, "/api/v3/movie", movie)

    async def trigger_movie_search(self, session: aiohttp.ClientSession, movie_id: int):
        return await self._post(session, "/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})

    async def get_command(self, session: aiohttp.ClientSession, command_id: int):
        return await self._get(session, f"/api/v3/command/{command_id}")

    async def get_queue(self, session: aiohttp.ClientSession, movie_id: int):
        records = []
        page = 1
        while True:
            data = await self._get(
                session, "/api/v3/queue",
                movieIds=str(movie_id), page=str(page), pageSize="200",
            )
            batch = data.get("records", [])
            records.extend(batch)
            # An empty page also ends the walk: totalRecords counts unfiltered
            # items in some Radarr versions, which a filtered walk never reaches.
            if not batch or len(records) >= data.get("totalRecords", len(records)):
                return records
            page += 1
