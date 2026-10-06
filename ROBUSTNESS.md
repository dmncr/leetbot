# Robustness review

The original bot kept four mutable score dictionaries and contestant logs in one JSON file, wrote that file in place after each attempt, and used an independent thread for scheduled IRC messages. The replacement retains the game and commands while moving persistence, analytics, and presentation into separate modules.

| Finding in the original code | Change |
| --- | --- |
| Interrupted writes could truncate scores; invalid JSON silently discarded history on load. | SQLite commits each attempt transactionally. Import validates the entire JSON in one transaction, fails startup on malformed history, and never modifies the source file. |
| Every attempt rewrote the entire history. Concurrent readers could observe mutable dictionaries. | Indexed tables, connection per operation, foreign keys, WAL, a 10-second lock timeout, and a bounded statistics cache. |
| Weekly and monthly participation could combine different calendar years; weekly keys used the calendar year rather than the ISO year. | Periods derive from actual game dates with exclusive upper bounds. Weeks run Monday–Sunday across year boundaries. |
| January's previous-month announcement incorrectly used December of the current year. | Previous month and year use the actual previous date. |
| The README said 13:37:37.037, but code targeted 13:37:37.000037. | New attempts target exactly 13:37:37.000000, matching the requested game. Imported scores remain untouched. |
| An attempt 13 seconds away earned 0, while one farther away earned 1. | New scores have a monotonic 1-point floor. |
| Encoding handling monkey-patched reactor internals and could accumulate wrappers after reconnects. | A replacement-decoding buffer class is installed per connection and survives reconnection. |
| Announcement threads slept for many hours, wrote to the socket outside the reactor, and could accumulate after restarts. | Reactor ticks perform scheduling and queued output. Calendar clocks are timezone-aware. Framework reconnects use an independent backoff object; socket stalls are bounded. |
| Output could exceed IRC line limits or flood the channel. | UTF-8 byte-aware splitting, control-character removal, one queued line per second, bounded queue, and command cooldown. Attempts bypass the command cooldown. |
| Being kicked left the bot outside the competition channel. | Automatic rejoin after five seconds; output waits until the bot has joined. |
| Restart could repeat daily announcements. | Persistent date/kind claims suppress duplicate scheduling across process restarts. |
| No web access controls or production HTTP server existed. | Waitress, shared-password authentication, constant-time credential comparison, signed 12-hour cookies, CSRF tokens, login throttling, security headers, and password-change session invalidation. |
| Python 3.8 image, unpinned direct packages, root execution, and exposed outbound IRC port. | Python 3.13, pinned direct requirements, UID/GID 1000:1000 execution, persistent named volume, HTTP health check, and only the stats port published. |
| A database created by a different user, or an unwritable database directory, caused an opaque startup exception. | Permission failures now identify the database, directory, runtime identity, and WAL requirements. The README includes a repair command for existing mounts. |

## Data integrity and migration

The supplied current file was checked as 422 recorded attempts, 294 active days, seven distinct nicknames, and 663 stored period/player summaries. It spans 2025-03-31 through 2026-10-03. All recorded scores and timestamps are imported unchanged. Some historical scores do not agree with their timestamps under the current rules, so score rankings and timing rankings intentionally measure different things.

`players` stores IRC nickname identity, `attempts` stores individual messages and signed microsecond offsets, `legacy_period_scores` preserves the old aggregate highs, `announcements` stores scheduling claims, and `metadata` stores schema-related configuration and the completed import fingerprint/counts. Summary-only daily records are flagged and excluded from attendance, attempt averages, and timing statistics. Weekly/monthly/yearly aggregate-only records remain separate. No synthetic attempt or invented timestamp is used to fill a missing log entry.

The importer uses an immediate transaction and a persistent completion marker, so simultaneous starts cannot duplicate history. Invalid input rolls back all imported rows; a corrected file can be retried. Changing the source after an import does not reimport it into that database. The source stays read-only and remains the migration backup. The database's competition timezone is fixed once initialized to avoid silently reinterpreting calendar dates.

## Validation

The automated suite covers the supplied history when it is available locally, old numeric and object summaries, preservation of individual scores/timestamps, import idempotency and concurrent startup, failure rollback/retry, summary-only records, invalid scores, concurrent database access, IRC nickname folding, ISO week/year boundaries, game boundaries, exact target/scoring floor, encoding and IRC message splitting, required URL/password outputs, announcement deduplication, authentication/logout, CSRF, login throttling, password rotation, invalid API queries, and empty datasets.

Manual browser verification covers desktop/mobile layout, login, leaderboard period/date navigation, filtering, champions, charts, heatmap, empty selections, logout, and browser console errors. Container verification covers building the image, migration as the non-root account, HTTP access, SQLite integrity, persistence and idempotency across restart, and graceful termination with IRC isolated from the live channel.

## Practical limits

- Run one bot instance for each competition database. SQLite is intended for local disk storage; a network filesystem or multiple active bot replicas needs a different deployment design.
- Timing is measured when the bot receives a message. IRC/network latency, reactor processing delays, and host clock accuracy affect results; the host should have time synchronization enabled.
- Players are identified by RFC1459-folded nicknames. Nickname changes to different identities remain separate; IRC account authentication and alias merging are not inferred from the history.
- The shared password is deliberately printed to the IRC channel as requested. Use a password dedicated to these statistics. Serve public access over HTTPS and enable secure cookies.
- Login throttling uses the direct peer address and is process-local. A reverse proxy's clients share that throttle bucket; forwarded headers are not trusted by the application. A large public deployment should add throttling at its trusted proxy.
- Announcement scheduling claims are at most once: a crash between claiming and delivering a queued announcement can lose that announcement. Reconnects during 13:38:30–13:59:59 catch up that day's results; longer downtime is not backfilled. The health endpoint checks HTTP/database availability, not successful IRC registration.
- The web service shares a process with IRC; Waitress runs on a separate thread. Statistics auto-refresh every minute and may reuse data for up to 15 seconds. Detailed all-time analytics load recorded attempts into memory; attendance charts aggregate long histories into at most 500 plotted bins. This is appropriate for a small IRC competition; very large histories would benefit from SQL materialized aggregates and a separate web process.
