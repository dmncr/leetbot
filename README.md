# LeetBot / 1337 statistics

An IRC timing competition with transactional SQLite storage and a password-protected statistics site. Players send `1337`, `leet`, or a supported variation between **13:37:00 (inclusive) and 13:38:00 (exclusive)** in the configured competition timezone. The target is **13:37:37.000000**.

The site uses a dark documentation-style layout with blue/violet accents inspired by [BMad Method](https://docs.bmad-method.org/start/build-your-first-change/) and [Spec Kit](https://speckit.org). All fonts, styles, scripts, icons, and SVG charts are local; no CDN or external analytics is required.

## Run with Docker Compose

1. Copy `docker-compose.yaml.default` to `docker-compose.yaml` and `.env.example` to `.env`.
2. Set your IRC connection/channel, `STATS_PASSWORD`, and the publicly reachable `STATS_URL` in `.env`. The password is shared with IRC players by the bot. Single-quote values containing literal `$` or `#` in `.env`.
3. Keep your existing `scores.json` beside the compose file for first-start migration. For a fresh installation without JSON, remove the `/legacy/scores.json` bind mount from the compose file.
4. Start the services:

```sh
docker compose up -d --build
docker compose logs -f leetbot
```

Open `http://localhost:8080` and sign in with `STATS_PASSWORD`. The port binds to localhost by default. Set `WEB_BIND=0.0.0.0` to allow other machines to reach it, or put a reverse proxy in front of the localhost port. Set `STATS_URL` to the address players will actually use. For HTTPS access, set `COOKIE_SECURE=true`.

The bot and production Waitress web server run in one non-root container. IRC is outbound; only the web port is published. The web site continues serving while IRC reconnects.

## Upgrade an existing installation

Stop the old bot before taking the final copy of its scores, so it cannot keep writing to the legacy file after migration. Preserve that copy as `scores.json`, update the compose configuration, and start the new image.

On startup, the bot initializes `/data/leetbot.sqlite3` in the persistent `leetbot-data` volume and imports the JSON **once per database**. A single transaction imports all attempts and old period highs, then stores a completion marker, counts, and SHA-256 fingerprint. Subsequent starts skip the import, even if the old file has changed. Invalid JSON or invalid records abort startup and roll back the import; correcting the source allows a retry. Existing data is never silently reset.

The source JSON is mounted read-only and remains untouched. Once a successful import is confirmed in the logs, you may remove the legacy bind mount. Keep the database volume across upgrades. `docker compose down` preserves it; `docker compose down -v` deletes it.

The supplied current history was verified as **422 attempts, 294 active days, 7 nicknames**, spanning **2025-03-31 to 2026-10-03**, with **663 period/player summaries**. The importer supports both numeric legacy scores and `{score, timestamp}` objects.

## Dashboard

- Day, ISO week, month, year, and all-time leaderboards, with an anchor date, nickname search, and sortable score/attendance/win/streak columns.
- All-time high score and the closest timestamp in the selected period.
- Precision ranking by mean absolute distance from the target, with median, standard deviation, and personal best. The configurable minimum is five active days by default.
- Attendance graph for unique daily players and attempts; monthly aggregation counts player-days. Missing dates between recorded games are zero attendance.
- A calendar heatmap with selectable year and per-day details.
- Attempts by second, early/late/exact counts, hits within 100 ms, average score, and attendance streaks.
- Daily, monthly, and yearly champions, plus the last 30 attempts in the selection. Exact score ties share the win.
- Automatic refresh every minute. API results may be cached for 15 seconds.

Best single attempt determines a period's score ranking. Averages include **all** recorded attempts, including retries. Attendance counts a player once per game date. Daily wins can be shared; streaks count consecutive calendar days with a recorded attempt. Monthly/yearly champions cover the full calendar periods represented in the selection.

Historical scores are preserved exactly, even where an old scoring rule produced a score inconsistent with the timestamp. Timing statistics use the recorded timestamp's actual distance from 13:37:37. Legacy summary-only highs are preserved, but no attempts, attendance, or average timing are invented for them.

## Commands

| Command | Response |
| --- | --- |
| `!help` | Rules, commands, statistics URL and shared password |
| `!time`, `!timetest` | Bot time and competition timezone |
| `!topscore`, `!topscores`, `!highscores` | Current day/week/month/year top five, plus site URL and password |
| `!toptoday`, `!topweek`, `!topmonth`, `!topyear` | Period top five and recorded participation, plus site URL and password |
| `!statistics` | Lifetime statistics for the ten most active players, plus site URL and password |

The bot announces the game at 13:36 and daily results at 13:38:30. Sunday's results include the weekly scoreboard; the first of each month/year includes the previous period. Results include the site URL and password even when nobody played. IRC output is queued at one line per second, with a five-second channel command cooldown; competition attempts are always accepted within the game window.

New scores use `max(1, 100 × (1 − absolute_distance_seconds / 13))`. Exactly on target scores 100; the consolation floor is 1. The old code accidentally targeted 37 microseconds after the second, and the old README described 37 milliseconds; the new target is the exact second requested. Imported scores are never recomputed.

## Configuration

| Variable | Default / purpose |
| --- | --- |
| `IRC_SERVER`, `IRC_PORT` | IRC host; port 6667 |
| `IRC_CHANNEL`, `IRC_NICKNAME` | Competition channel and bot nickname |
| `GAME_TIMEZONE` | `Europe/Stockholm`; IANA timezone for all competition dates and scheduling |
| `TZ` | Fallback competition timezone if `GAME_TIMEZONE` is unset outside Compose |
| `STATS_PASSWORD` | Required shared login password; also printed to IRC |
| `STATS_URL` | `http://localhost:8080`; address announced to players |
| `DATABASE_PATH` | `data/leetbot.sqlite3` locally; `/data/leetbot.sqlite3` in Docker |
| `LEGACY_SCORES_PATH` | `scores.json` locally; `/legacy/scores.json` in Docker |
| `WEB_HOST`, `WEB_PORT` | Application bind host/port; `0.0.0.0:8080`. Compose's `WEB_PORT` changes the host port only. |
| `WEB_BIND` | Compose host interface, `127.0.0.1` |
| `COOKIE_SECURE` | `false`; set `true` for HTTPS |
| `SESSION_SECRET` | Optional signing secret; otherwise generated once in database metadata |

Changing `STATS_PASSWORD` invalidates existing signed sessions on restart. Sessions expire after 12 hours. Changing a database's competition timezone is rejected, since it would reinterpret historical calendar dates. Use a password dedicated to this channel's stats; it is intentionally advertised in IRC.

## Database and backups

`players` stores case-folded IRC nickname identity. `attempts` stores player, local game date/time, UTC-offset-bearing receipt timestamp for new attempts, signed integer microsecond distance, score, and provenance. `legacy_period_scores` archives the original period highs. `metadata` records import/configuration/signing information; `announcements` prevents duplicate scheduling after a restart. Each attempt commits transactionally, with WAL and indexed date/player queries.

Use one bot instance and a local disk volume. Unrelated nicknames remain separate players; IRC RFC1459 case variants are merged. See [ROBUSTNESS.md](ROBUSTNESS.md) for the review, fixes, verification, and operational limits.

To make a consistent SQLite backup while the bot is running:

```sh
docker compose exec -T leetbot python -c "import sqlite3; src=sqlite3.connect('/data/leetbot.sqlite3'); dst=sqlite3.connect('/data/leetbot.sqlite3.backup'); src.backup(dst); dst.close(); src.close()"
docker compose cp leetbot:/data/leetbot.sqlite3.backup ./leetbot.sqlite3.backup
```

Restore with the container stopped. Retain the legacy JSON as a separate pre-migration backup. Avoid copying only the live `.sqlite3` file while WAL writes are active.

## Database permissions

The container runs as UID/GID `1000:1000`. SQLite needs write access to the database **and its containing directory**, because it creates `leetbot.sqlite3-wal` and `leetbot.sqlite3-shm` alongside the database. You do not need to create the database manually; `touch` can leave a file owned by the wrong user.

The supplied Compose configuration uses a named volume mounted read-write at `/data`; a fresh named volume inherits the image's directory ownership. An existing volume or a host bind mount may have different ownership, including `10001:10001` from the earlier image. Mount the whole directory (for example `./data:/data`), rather than mounting just the SQLite file. Only the legacy JSON should be mounted read-only.

For the default database path `/data/leetbot.sqlite3`, repair an existing writable mount without deleting the database:

```sh
docker compose stop leetbot
docker compose run --rm --no-deps --user 0 --entrypoint sh leetbot -c 'set -eu; chown 1000:1000 /data; chmod u+rwx /data; for f in /data/leetbot.sqlite3 /data/leetbot.sqlite3-wal /data/leetbot.sqlite3-shm; do if [ -f "$f" ]; then chown 1000:1000 "$f"; chmod u+rw "$f"; fi; done'
docker compose up -d leetbot
```

Use your actual database path/directory if you changed `DATABASE_PATH`. This command changes ownership of a host bind mount's directory/files too. If it reports a read-only filesystem, remove `:ro`/`read_only: true` from the database mount first. Do not delete the database or its volume to fix permissions.

## Development and verification

Requires Python 3.13. Create a virtual environment and install `requirements-dev.txt`, then run:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The private live-file migration regression runs when `scores.json` exists locally and otherwise skips; synthetic migration, authentication, statistics, and IRC regressions remain runnable without it. Tests never connect to a live IRC channel.

Run `python leetbot.py` with the environment variables above for local operation. `GET /healthz` checks web/database availability without exposing scores; `GET /api/stats` requires authentication. A failed configuration or migration exits the process so the container restart policy can handle it.

## License

This project is open source and available under the MIT License.
