# sonarrbot

Discord bot that lets you search for and add TV shows to Sonarr via a slash command.

## Usage

`/addshow query:<title>` — searches Sonarr, lets you pick the matching show from a
dropdown, then pick a root folder and quality profile, then adds it and triggers a
search for missing episodes.

## Setup

1. **Create a Discord application/bot**: https://discord.com/developers/applications
   - New Application → Bot → reset/copy the token (this is `DISCORD_TOKEN`).
   - Under "Installation" / OAuth2 URL Generator, select scopes `bot` and
     `applications.commands`, give it the `Send Messages` permission, and use the
     generated URL to invite it to your server.
2. **Get your Sonarr API key**: Sonarr → Settings → General → Security → API Key.
3. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, `SONARR_URL`,
   `SONARR_API_KEY`. `GUILD_ID` is optional — set it to your server's ID while testing
   so the slash command registers instantly instead of waiting up to an hour.
4. Run it:

   ```
   docker compose up -d --build
   ```

`SONARR_URL` must be reachable from the bot container — use the NAS's LAN IP
(e.g. `http://192.168.1.10:8989`) unless Sonarr and this bot share a Docker network.
