![ArrBridge](Logos/banner.png)

# ArrBridge

Discord bot that lets you search for and add TV shows to Sonarr, and movies to Radarr,
via slash commands.

## Usage

`/addshow query:<title>` — searches Sonarr, lets you pick the matching show from a
dropdown, then pick a root folder and quality profile, then adds it and triggers a
search for missing episodes. The reply keeps updating live with download progress.

`/tvstatus query:<title>` — looks up a show already in your Sonarr library and shows
its current monitored/download status, without needing to add it or open Sonarr.

`/addmovie query:<title>` — same flow as `/addshow`, but searches Radarr and adds a
movie.

`/moviestatus query:<title>` — same as `/tvstatus`, but for a movie already in Radarr.

Separately, a background task sweeps the channel set by `CLEANUP_CHANNEL_ID` every
30 minutes and deletes every message older than `CLEANUP_MAX_AGE_HOURS` (default 6),
so that channel never builds up history. This is not tied to a command.

## Setup

1. **Create a Discord application/bot**: https://discord.com/developers/applications
   - New Application → Bot → reset/copy the token (this is `DISCORD_TOKEN`).
   - Under "Installation" / OAuth2 URL Generator, select scopes `bot` and
     `applications.commands`, give it the `Send Messages` and `Manage Messages`
     permissions (the latter is required for channel cleanup to delete other users'
     messages), and use the generated URL to invite it to your server.
2. **Get your Sonarr and Radarr API keys**: Settings → General → Security → API Key
   in each app.
3. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, `SONARR_URL`,
   `SONARR_API_KEY`, `RADARR_URL`, `RADARR_API_KEY`, `CLEANUP_CHANNEL_ID` (right-click
   the channel in Discord with Developer Mode on → Copy Channel ID).  `GUILD_ID` is
   optional — set it to your server's ID while testing so slash commands register
   instantly instead of waiting up to an hour.
4. Run it from the repo directory (where `docker-compose.yml` and `.env` live):

   ```
   # Use the prebuilt image from GHCR (rebuilt on every push to main)
   docker compose up -d

   # ...or build the image yourself from this source tree
   docker compose up -d --build
   ```

   To update the prebuilt image later: `docker compose pull && docker compose up -d`.

`SONARR_URL`/`RADARR_URL` must be reachable from the bot container — use the host's
LAN IP (e.g. `http://192.168.1.10:8989`) unless Sonarr/Radarr and this bot share a
Docker network.

`.env` is read at runtime via `env_file` and is never baked into the image:
`.dockerignore` excludes it (along with `.git`, `.claude/` and `__pycache__/`) from
local builds, so building from source won't leak your tokens into an image.

Download progress embeds that are still updating when the bot restarts are recorded
in `data/pending.json` (mounted from `./data` on the host) and pick up where they
left off once the bot reconnects.

Host-specific settings (Unraid labels, extra networks, etc.) belong in a
`docker-compose.override.yml`, which Compose merges automatically and git ignores.

## Troubleshooting

- **Bot restarts repeatedly / `Cannot connect to host discord.com` / `Temporary failure
  in name resolution`** — the container can't resolve DNS. `docker-compose.yml` pins
  `dns: [1.1.1.1, 8.8.8.8]` for this reason; if your network blocks public DNS,
  replace those with your router's or local resolver's IP. Note that public resolvers
  can't resolve local hostnames, so use IPs (not `sonarr.lan`) in `SONARR_URL`/`RADARR_URL`.
- **Slash commands don't appear** — set `GUILD_ID` for instant sync; global sync can
  take up to an hour.
- **Channel cleanup deletes nothing** — the bot needs View Channel, Read Message
  History and Manage Messages in that channel; the log names whichever is missing.
- Check logs with `docker compose logs -f arrbridge`.
