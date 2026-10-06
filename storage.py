"""Transactional SQLite storage and a one-time, lossless legacy import."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

from game import nick_key, period_bounds, period_key, timing

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY, nick_key TEXT NOT NULL UNIQUE, nick TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    game_date TEXT NOT NULL,
    received_at TEXT,
    local_time TEXT,
    offset_us INTEGER,
    score REAL NOT NULL CHECK(score >= 0 AND score <= 100),
    source TEXT NOT NULL CHECK(source IN ('live', 'legacy', 'legacy_summary')),
    import_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS attempts_date ON attempts(game_date, player_id);
CREATE INDEX IF NOT EXISTS attempts_player ON attempts(player_id, game_date);
CREATE TABLE IF NOT EXISTS legacy_period_scores (
    period TEXT NOT NULL, period_key TEXT NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(id),
    score REAL NOT NULL CHECK(score >= 0 AND score <= 100), local_time TEXT,
    PRIMARY KEY(period, period_key, player_id)
);
CREATE TABLE IF NOT EXISTS announcements (
    game_date TEXT NOT NULL, kind TEXT NOT NULL,
    PRIMARY KEY(game_date, kind)
);
"""


class ScoreStore:
    def __init__(self, path='data/leetbot.sqlite3', timezone='Europe/Stockholm'):
        self.path = str(path)
        self.timezone = timezone
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except PermissionError as exc:
            raise PermissionError(self._permission_message()) from exc
        except sqlite3.OperationalError as exc:
            code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
            if code in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_PERM):
                raise PermissionError(self._permission_message()) from exc
            raise

    def _permission_message(self):
        path = Path(self.path).resolve()
        identity = f'UID {os.getuid()}, GID {os.getgid()}' if hasattr(os, 'getuid') else 'the current user'
        return (f'Cannot open or write SQLite database {path} as {identity}. '
                f'Both the database file and its directory ({path.parent}) must be writable; '
                'SQLite creates -wal and -shm files beside the database. '
                'Mount the database directory read-write and fix its ownership/permissions '
                '(the Docker image uses UID/GID 1000:1000). Creating an empty file with touch '
                'does not fix this. See README.md: Database permissions.')

    def _initialize(self):
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f'Unsupported database schema version: {version}')
            db.executescript(SCHEMA)
            db.execute('PRAGMA user_version=1')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('game_timezone', ?)", (self.timezone,))
            stored = db.execute("SELECT value FROM metadata WHERE key='game_timezone'").fetchone()[0]
            if stored != self.timezone:
                raise ValueError(f'Database uses {stored}; changing GAME_TIMEZONE would reinterpret history')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _player(db, nick):
        if not isinstance(nick, str) or not nick.strip() or len(nick) > 128 or any(ord(c) < 32 for c in nick):
            raise ValueError('Invalid nickname')
        key = nick_key(nick)
        db.execute('INSERT OR IGNORE INTO players(nick_key,nick) VALUES (?,?)', (key, nick))
        return db.execute('SELECT id FROM players WHERE nick_key=?', (key,)).fetchone()[0]

    @staticmethod
    def _score(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f'Invalid legacy score: {value!r}')
        return float(value)

    def add_attempt(self, nick, score, timestamp):
        with self.connect() as db:
            player = self._player(db, nick)
            db.execute('''INSERT INTO attempts
                (player_id,game_date,received_at,local_time,offset_us,score,source)
                VALUES (?,?,?,?,?,?, 'live')''',
                (player, timestamp.date().isoformat(), timestamp.isoformat(),
                 timestamp.strftime('%H:%M:%S.%f'), timing(timestamp), self._score(score)))

    def migrate_json(self, path):
        """Import once in one transaction. Leave the supplied JSON untouched.

        Period summaries are archived separately rather than invented as attempts.
        Daily bests missing from the attempt log become explicitly marked summaries.
        Invalid input aborts the entire import and startup, never resets history.
        """
        with self.connect() as db:
            # Lock before checking the marker: simultaneous starts cannot double import.
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM metadata WHERE key='legacy_import'").fetchone():
                return False
            source = Path(path)
            if not source.exists():
                return False
            raw = source.read_bytes()
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get('daily'), dict):
                raise ValueError('Legacy file must contain a daily object')
            for period in ('daily', 'weekly', 'monthly', 'yearly'):
                if not isinstance(data.get(period, {}), dict):
                    raise ValueError(f'Invalid {period} object')
            counts = {'attempts': 0, 'daily_summaries': 0, 'period_summaries': 0}
            for key, entries in data['daily'].items():
                if not key.endswith('_contestants'):
                    continue
                day = self._legacy_date(key.removesuffix('_contestants'))
                if not isinstance(entries, list):
                    raise ValueError(f'Invalid contestants list: {key}')
                for index, entry in enumerate(entries):
                    player = self._player(db, entry['nick'])
                    score = self._score(entry['score'])
                    stamp = self._legacy_stamp(day, entry.get('timestamp'))
                    if stamp is None:
                        raise ValueError(f'Missing attempt timestamp: {key}[{index}]')
                    db.execute('''INSERT INTO attempts
                        (player_id, game_date, local_time, offset_us, score, source, import_key)
                        VALUES (?,?,?,?,?, 'legacy',?)''',
                        (player, day.isoformat(), stamp.strftime('%H:%M:%S.%f'), timing(stamp),
                         score, f'{key}:{index}'))
                    counts['attempts'] += 1
            for period in ('daily', 'weekly', 'monthly', 'yearly'):
                for key, players in data.get(period, {}).items():
                    if key.endswith('_contestants'):
                        if period != 'daily':
                            raise ValueError('Contestants must be in daily')
                        continue
                    normalized = self._legacy_key(period, key)
                    if not isinstance(players, dict):
                        raise ValueError(f'Invalid summary: {period}/{key}')
                    for nick, value in players.items():
                        player = self._player(db, nick)
                        score = self._score(value['score'] if isinstance(value, dict) else value)
                        local_time = value.get('timestamp') if isinstance(value, dict) else None
                        stamp = self._legacy_stamp(date(2000, 1, 1), local_time)
                        local_time = stamp.strftime('%H:%M:%S.%f') if stamp else None
                        db.execute('''INSERT INTO legacy_period_scores VALUES (?,?,?,?,?)
                            ON CONFLICT(period,period_key,player_id) DO UPDATE SET
                            score=max(score,excluded.score),
                            local_time=CASE WHEN excluded.score>score THEN excluded.local_time ELSE local_time END''',
                            (period, normalized, player, score, local_time))
                        counts['period_summaries'] += 1
                        if period == 'daily':
                            existing = db.execute('SELECT max(score) FROM attempts WHERE player_id=? AND game_date=?',
                                                  (player, normalized)).fetchone()[0]
                            if existing is None or score > existing:
                                db.execute('''INSERT INTO attempts
                                    (player_id,game_date,local_time,score,source,import_key)
                                    VALUES (?,?,?,?, 'legacy_summary',?)''',
                                    (player, normalized, local_time, score, f'daily:{key}:{nick}'))
                                counts['daily_summaries'] += 1
            counts['sha256'] = hashlib.sha256(raw).hexdigest()
            counts['source'] = str(source)
            db.execute("INSERT INTO metadata VALUES ('legacy_import',?)", (json.dumps(counts),))
            return counts

    @staticmethod
    def _legacy_date(value):
        return date(*map(int, value.split('-')))

    @staticmethod
    def _legacy_stamp(day, value):
        if value is None:
            return None
        return datetime.combine(day, datetime.strptime(value, '%H:%M:%S.%f' if '.' in value else '%H:%M:%S').time())

    @classmethod
    def _legacy_key(cls, period, key):
        if period == 'daily':
            return cls._legacy_date(key).isoformat()
        if period == 'monthly':
            year, month = map(int, key.split('-'))
            return date(year, month, 1).strftime('%Y-%m')
        if period == 'weekly':
            year, week = map(int, key.split('-W'))
            date.fromisocalendar(year, week, 1)
            return f'{year}-W{week:02}'
        return str(date(int(key), 1, 1).year)

    def leaderboard(self, period, anchor):
        start, end = period_bounds(period, anchor)
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('''WITH ranked AS (
                SELECT a.*, row_number() OVER (PARTITION BY player_id ORDER BY score DESC, id) AS rank
                FROM attempts a WHERE game_date>=? AND game_date<?
            ), totals AS (
                SELECT player_id, sum(source!='legacy_summary') AS attempts,
                       count(DISTINCT CASE WHEN source!='legacy_summary' THEN game_date END) AS days,
                       avg(CASE WHEN source!='legacy_summary' THEN score END) AS average_score,
                       avg(abs(offset_us))/1000.0 AS average_offset_ms,
                       min(abs(offset_us))/1000.0 AS closest_ms
                FROM ranked GROUP BY player_id
            ) SELECT p.id AS player_id,p.nick,r.score,r.local_time,r.game_date,t.attempts,t.days,
                     t.average_score,t.average_offset_ms,t.closest_ms
                FROM ranked r JOIN players p ON p.id=r.player_id JOIN totals t USING(player_id)
                WHERE r.rank=1 ORDER BY r.score DESC,p.nick''', (start, end))]
            # Retain historical aggregate highs even when the original log is incomplete.
            names = {'day': 'daily', 'today': 'daily', 'week': 'weekly', 'month': 'monthly', 'year': 'yearly'}
            kind = names.get(period, period)
            if period == 'all':
                archives = db.execute('''SELECT l.*,p.nick FROM legacy_period_scores l
                    JOIN players p ON p.id=l.player_id''').fetchall()
            else:
                archives = db.execute('''SELECT l.*,p.nick FROM legacy_period_scores l
                    JOIN players p ON p.id=l.player_id WHERE period=? AND period_key=?''',
                    (kind, period_key(kind, anchor))).fetchall()
            by_player = {r['player_id']: r for r in rows}
            for item in archives:
                current = by_player.get(item['player_id'])
                if current is None:
                    current = {'player_id': item['player_id'], 'nick': item['nick'], 'attempts': 0,
                               'days': 0, 'average_score': None, 'average_offset_ms': None, 'closest_ms': None}
                    by_player[item['player_id']] = current
                if 'score' not in current or item['score'] > current['score']:
                    current.update(score=item['score'], local_time=item['local_time'], game_date=None)
            return sorted(by_player.values(), key=lambda r: (-r['score'], nick_key(r['nick'])))

    def attempts(self, period, anchor):
        start, end = period_bounds(period, anchor)
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT a.*,p.nick FROM attempts a
                JOIN players p ON p.id=a.player_id WHERE game_date>=? AND game_date<?
                AND source!='legacy_summary' ORDER BY game_date,local_time,id''', (start, end))]

    def claim_announcement(self, day, kind):
        with self.connect() as db:
            return db.execute('INSERT OR IGNORE INTO announcements VALUES (?,?)', (day.isoformat(), kind)).rowcount == 1

    def calendar_winners(self, period, scope, anchor):
        """Calendar highs including summary-only history; never fabricate attendance."""
        start, end = period_bounds(scope, anchor)
        length = {'daily': 10, 'monthly': 7, 'yearly': 4}[period]
        last = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
        with self.connect() as db:
            known = db.execute('''SELECT substr(game_date,1,?) AS bucket,player_id,max(score) AS score
                FROM attempts WHERE substr(game_date,1,?)>=? AND substr(game_date,1,?)<=?
                GROUP BY bucket,player_id''', (length, length, start[:length], length, last[:length])).fetchall()
            archived = db.execute('''SELECT period_key AS bucket,player_id,score FROM legacy_period_scores
                WHERE period=? AND period_key>=? AND period_key<=?''',
                (period, start[:length], last[:length])).fetchall()
            names = dict(db.execute('SELECT id,nick FROM players'))
        buckets = {}
        for row in [*known, *archived]:
            players = buckets.setdefault(row['bucket'], {})
            players[row['player_id']] = max(players.get(row['player_id'], -1), row['score'])
        result = []
        for bucket, players in sorted(buckets.items(), reverse=True):
            highest = max(players.values())
            ids = [player for player, score in players.items() if score == highest]
            result.append({'period': bucket, 'nicks': [names[player] for player in ids],
                           'player_ids': ids, 'score': highest})
        return result

    def metadata(self):
        with self.connect() as db:
            return dict(db.execute('SELECT key,value FROM metadata'))
