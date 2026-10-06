"""IRC competition bot. Socket writes and announcements stay on the reactor."""
from collections import deque
from datetime import datetime, timedelta
import logging
import os
import signal
import socket
import threading
import time
from zoneinfo import ZoneInfo

import irc.bot
import irc.client
from jaraco.stream.buffer import DecodingLineBuffer
from waitress import create_server

from game import LEET, calculate_score, nick_key
from storage import ScoreStore
from webapp import create_app

logger = logging.getLogger(__name__)


class SafeBuffer(DecodingLineBuffer):
    errors = 'replace'


def connect_socket(address):
    # Bound connect/send/receive stalls, including during graceful shutdown.
    return socket.create_connection(address, timeout=5)


class LeetBot(irc.bot.SingleServerIRCBot):
    def __init__(self, channel, nickname, server, port=6667, *, store=None,
                 timezone=None, stats_url=None, stats_password=None):
        super().__init__([(server, port)], nickname, nickname, recon=irc.bot.ExponentialBackoff(),
                         connect_factory=connect_socket)
        self.connection.buffer_class = SafeBuffer
        self.channel = channel
        self.timezone = ZoneInfo(timezone or os.getenv('GAME_TIMEZONE', os.getenv('TZ', 'Europe/Stockholm')))
        self.store = store or ScoreStore(os.getenv('DATABASE_PATH', 'data/leetbot.sqlite3'), str(self.timezone))
        self.stats_url = stats_url if stats_url is not None else os.getenv('STATS_URL', 'http://localhost:8080')
        self.stats_password = stats_password if stats_password is not None else os.getenv('STATS_PASSWORD', '')
        for value in (self.stats_url, self.stats_password):
            if any(ord(c) < 32 for c in value):
                raise ValueError('Stats URL/password cannot contain control characters')
        self.outbox = deque(maxlen=500)
        self.last_command = 0.0
        self.joined = False
        self.reactor.scheduler.execute_every(1, self.tick)

    def now(self):
        return datetime.now(self.timezone)

    @staticmethod
    def is_leet_message(message):
        return bool(LEET.search(message))

    calculate_score = staticmethod(calculate_score)

    def update_scores(self, nick, score, timestamp):
        self.store.add_attempt(nick, score, timestamp)

    def say(self, target, message):
        # Leave room for server prefix; split by UTF-8 bytes without splitting characters.
        clean = ''.join(c for c in str(message) if ord(c) >= 32)
        if any(c in target for c in '\r\n\0 '):
            raise ValueError('Invalid IRC target')
        budget = max(64, 380 - len(target.encode('utf-8')))
        chunk, size = '', 0
        for char in clean:
            width = len(char.encode('utf-8'))
            if size + width > budget:
                self.outbox.append((target, chunk))
                chunk, size = '', 0
            chunk += char
            size += width
        if chunk:
            self.outbox.append((target, chunk))

    def send_stats_link(self, target):
        self.say(target, f'Stats: {self.stats_url} | Login password: {self.stats_password}')

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname()[:25] + '_')

    def on_welcome(self, c, e):
        c.join(self.channel)

    def on_join(self, c, e):
        if nick_key(e.source.nick) == nick_key(c.get_nickname()):
            self.joined = True
            self.say(self.channel, "LeetBot is online! Type '!help' for commands.")

    def on_kick(self, c, e):
        if nick_key(e.arguments[0]) == nick_key(c.get_nickname()):
            self.joined = False
            self.reactor.scheduler.execute_after(5, lambda: c.join(self.channel) if c.is_connected() else None)

    def on_disconnect(self, c, e):
        self.joined = False
        self.outbox.clear()
        logger.warning('IRC disconnected; framework will reconnect with backoff')

    def on_pubmsg(self, c, e):
        now = self.now()  # Capture receipt time before processing or storage.
        try:
            if nick_key(e.target) != nick_key(self.channel) or not e.source or not e.arguments:
                return
            nick = e.source.nick
            if nick_key(nick) == nick_key(c.get_nickname()):
                return
            message = e.arguments[0].strip()
            start = now.replace(hour=13, minute=37, second=0, microsecond=0)
            end = start + timedelta(minutes=1)
            if start <= now < end and self.is_leet_message(message):
                self.update_scores(nick, self.calculate_score(now), now)
                return
            command = message.lower()
            commands = {'!help', '!time', '!timetest', '!highscores', '!topscore', '!topscores',
                        '!toptoday', '!topweek', '!topmonth', '!topyear', '!statistics'}
            if command not in commands:
                return
            if time.monotonic() - self.last_command < 5 or len(self.outbox) > 50:
                return
            self.last_command = time.monotonic()
            if command == '!help':
                self.send_help(e)
            elif command in ('!time', '!timetest'):
                self.send_time(e, now)
            elif command in ('!highscores', '!topscore', '!topscores'):
                self.send_highscores(e)
            elif command == '!statistics':
                self.send_statistics(e)
            else:
                self.send_top_scores(e, command[4:])
        except Exception:
            logger.exception('Failed to process IRC message')

    def send_help(self, e):
        self.say(e.target, 'Commands: !help, !time / !timetest, !topscore / !highscores, '
                 '!toptoday, !topweek, !topmonth, !topyear, !statistics.')
        self.say(e.target, f'Type 1337 or leet during 13:37:00 <= time < 13:38:00 ({self.timezone}). '
                 'Target: 13:37:37.000000. Best attempt per period wins; retries are allowed.')
        self.send_stats_link(e.target)

    def send_time(self, e, timestamp):
        self.say(e.target, f'Server receipt time: {timestamp:%H:%M:%S.%f} ({self.timezone})')

    def _scoreboard(self, target, period, anchor, title):
        rows = self.store.leaderboard(period, anchor)
        if not rows:
            self.say(target, f'{title}: No participants.')
        else:
            self.say(target, f'{title} - Top 5:')
            for rank, row in enumerate(rows[:5], 1):
                stamp = f" at {row['local_time']}" if row.get('local_time') else ''
                self.say(target, f"{rank}. {row['nick']}: {row['score']:.2f}{stamp} | "
                         f"{row['attempts']} attempts / {row['days']} days")

    def send_highscores(self, e):
        now = self.now().date()
        for period, title in [('day', 'Today'), ('week', 'This week'), ('month', 'This month'), ('year', 'This year')]:
            self._scoreboard(e.target, period, now, title)
        self.send_stats_link(e.target)

    def send_top_scores(self, e, period):
        self._scoreboard(e.target, period, self.now().date(), f'Top {period}')
        self.send_stats_link(e.target)

    def send_statistics(self, e):
        rows = sorted(self.store.leaderboard('all', self.now().date()), key=lambda r: (-r['attempts'], -r['score']))
        self.say(e.target, 'Lifetime statistics (top 10 by attendance):')
        for row in rows[:10]:
            avg = f"{row['average_score']:.2f}" if row['average_score'] is not None else 'unknown'
            self.say(e.target, f"{row['nick']}: {row['attempts']} attempts, {row['days']} days, "
                     f"best {row['score']:.2f}, average {avg}")
        self.send_stats_link(e.target)

    def make_announcements(self, now=None):
        now = now or self.now()
        self._scoreboard(self.channel, 'day', now.date(), "Today's scoreboard")
        if now.weekday() == 6:
            self._scoreboard(self.channel, 'week', now.date(), 'Weekly scoreboard')
        if now.day == 1:
            self._scoreboard(self.channel, 'month', now.date() - timedelta(days=1), 'Last month')
        if now.month == 1 and now.day == 1:
            self._scoreboard(self.channel, 'year', now.date() - timedelta(days=1), 'Last year')
        self.send_stats_link(self.channel)

    def tick(self):
        try:
            if not self.connection.is_connected() or not self.joined:
                return
            now = self.now()
            if now.hour == 13 and now.minute == 36 and self.store.claim_announcement(now.date(), 'pregame'):
                self.say(self.channel, f'The game is about to begin! Target 13:37:37 ({self.timezone}).')
            if (now.hour == 13 and (now.minute > 38 or (now.minute == 38 and now.second >= 30))
                    and self.store.claim_announcement(now.date(), 'results')):
                self.make_announcements(now)
            if self.outbox:
                target, message = self.outbox.popleft()
                self.connection.privmsg(target, message)
        except Exception:
            logger.exception('IRC tick failed; retrying on next tick')


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    timezone = os.getenv('GAME_TIMEZONE', os.getenv('TZ', 'Europe/Stockholm'))
    ZoneInfo(timezone)
    password = os.environ.get('STATS_PASSWORD', '')
    if not password:
        raise ValueError('STATS_PASSWORD must be set')
    if any(ord(c) < 32 for c in password):
        raise ValueError('STATS_PASSWORD cannot contain control characters')
    store = ScoreStore(os.getenv('DATABASE_PATH', 'data/leetbot.sqlite3'), timezone)
    result = store.migrate_json(os.getenv('LEGACY_SCORES_PATH', 'scores.json'))
    if result:
        logger.info('Legacy import complete: %s', result)
    app = create_app(store, password=password)
    server = create_server(app, host=os.getenv('WEB_HOST', '0.0.0.0'), port=int(os.getenv('WEB_PORT', '8080')))
    worker = threading.Thread(target=server.run, name='stats-web', daemon=True)
    worker.start()
    bot = LeetBot(os.getenv('IRC_CHANNEL', '#mbhooden'), os.getenv('IRC_NICKNAME', 'LeetBot1337'),
                  os.getenv('IRC_SERVER', 'portlane.se.quakenet.org'), int(os.getenv('IRC_PORT', '6667')), store=store)
    stopping = threading.Event()

    def stop(*_):
        stopping.set()
        bot.joined = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        bot._connect()
        while not stopping.is_set():
            if not worker.is_alive():
                raise RuntimeError('Stats server stopped unexpectedly')
            bot.reactor.process_once(timeout=0.2)
    finally:
        if bot.connection.is_connected():
            bot.connection.disconnect('LeetBot shutting down')
        server.close()


if __name__ == '__main__':
    main()
