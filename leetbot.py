"""IRC competition bot. Socket writes and announcements stay on the reactor."""
from collections import OrderedDict, deque
from datetime import datetime, timedelta
import logging
import os
import re
import secrets
import signal
import socket
import threading
import time
from zoneinfo import ZoneInfo

import irc.bot
import irc.client
from jaraco.stream.buffer import DecodingLineBuffer
from waitress import create_server

from clocktest import CLOCK_TEST_TTL, ClockProbe, ClockTest, epoch_us
from game import LEET, calculate_score, nick_key
from storage import ScoreStore
from webapp import create_app

logger = logging.getLogger(__name__)
COMMANDS = {'!help': 'help', '!time': 'time', '!leetbottime': 'time',
            '!highscores': 'highscores', '!topscore': 'highscores', '!topscores': 'highscores',
            '!toptoday': 'today', '!topweek': 'week', '!topmonth': 'month', '!topyear': 'year',
            '!statistics': 'statistics', '!timetest': 'clock'}


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
        self.command_times = OrderedDict()
        self.clock_tests = OrderedDict()
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

    def say(self, target, message, *, priority=False):
        # Leave room for server prefix; split by UTF-8 bytes without splitting characters.
        clean = ''.join(c for c in str(message) if ord(c) >= 32)
        if any(c in target for c in '\r\n\0 '):
            raise ValueError('Invalid IRC target')
        budget = max(64, 380 - len(target.encode('utf-8')))
        chunks, chunk, size = [], '', 0
        for char in clean:
            width = len(char.encode('utf-8'))
            if size + width > budget:
                chunks.append((target, chunk))
                chunk, size = '', 0
            chunk += char
            size += width
        if chunk:
            chunks.append((target, chunk))
        if priority:
            self.outbox.extendleft(reversed(chunks))
        else:
            self.outbox.extend(chunks)

    def send_stats_link(self, target):
        self.say(target, f'Stats: {self.stats_url} | Login password: {self.stats_password}')

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname()[:25] + '_')

    def on_welcome(self, c, e):
        logger.info('IRC registered as %s; joining %s', c.get_nickname(), self.channel)
        c.join(self.channel)

    def on_join(self, c, e):
        if nick_key(e.source.nick) == nick_key(c.get_nickname()):
            self.joined = True
            logger.info('IRC joined %s as %s', self.channel, c.get_nickname())
            self.say(self.channel, "LeetBot is online! Type '!help' for commands.")

    def on_kick(self, c, e):
        if nick_key(e.arguments[0]) == nick_key(c.get_nickname()):
            self.joined = False
            self.reactor.scheduler.execute_after(5, lambda: c.join(self.channel) if c.is_connected() else None)

    def on_disconnect(self, c, e):
        self.joined = False
        self.outbox.clear()
        self.clock_tests.clear()
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
            if self.handle_command(e, now, message):
                return
            start = now.replace(hour=13, minute=37, second=0, microsecond=0)
            end = start + timedelta(minutes=1)
            if start <= now < end and self.is_leet_message(message):
                self.update_scores(nick, self.calculate_score(now), now)
                return
        except Exception:
            logger.exception('Failed to process IRC message')

    def on_privmsg(self, c, e):
        now = self.now()
        try:
            if not e.source or not e.arguments or nick_key(e.source.nick) == nick_key(c.get_nickname()):
                return
            # Commands sent in a query also answer in the competition channel.
            # Private 1337 messages never count as competition attempts.
            self.handle_command(e, now, e.arguments[0].strip())
        except Exception:
            logger.exception('Failed to process private IRC command')

    def command_allowed(self, nick, kind):
        key, now = (nick_key(nick), kind), time.monotonic()
        previous = self.command_times.get(key)
        if previous is not None and now - previous < 5:
            return False
        self.command_times[key] = now
        self.command_times.move_to_end(key)
        while len(self.command_times) > 4096:
            self.command_times.popitem(last=False)
        return True

    def handle_command(self, e, now, message):
        parts = message.split()
        if not parts or parts[0].lower() not in COMMANDS:
            return False
        kind = COMMANDS[parts[0].lower()]
        if kind == 'clock' and len(parts) > 1:
            # A valid one-use callback bypasses the request cooldown so pasting
            # immediately works. Receipt time was captured before this handler.
            self.finish_clock_test(e, now, parts)
            return True
        if len(parts) != 1:
            return True
        if len(self.outbox) > 50 or not self.command_allowed(e.source.nick, kind):
            return True
        if kind == 'help':
            self.send_help(e)
        elif kind == 'time':
            self.send_time(e, now)
        elif kind == 'highscores':
            self.send_highscores(e)
        elif kind == 'statistics':
            self.send_statistics(e)
        elif kind == 'clock':
            self.start_clock_test(e)
        else:
            self.send_top_scores(e, kind)
        return True

    def expire_clock_tests(self):
        now = time.monotonic()
        for token, pending in list(self.clock_tests.items()):
            if pending.expires <= now:
                del self.clock_tests[token]

    def start_clock_test(self, e):
        self.expire_clock_tests()
        owner = nick_key(e.source.nick)
        # Keep one outstanding test per nickname and bound total memory.
        for token, pending in list(self.clock_tests.items()):
            if pending.owner == owner:
                del self.clock_tests[token]
        token = secrets.token_hex(6)
        self.clock_tests[token] = ClockTest(owner, e.source.nick, time.monotonic() + CLOCK_TEST_TTL)
        while len(self.clock_tests) > 256:
            self.clock_tests.popitem(last=False)
        self.say(self.channel, f'{e.source.nick}: Millisecond clock/latency test: '
                 f'{self.stats_url.rstrip("/")}/timetest/{token} '
                 '(same site login). Copy the mIRC snippet there, then run '
                 f'/leetclock_{token} in {self.channel}. One-use ID; expires in 5 minutes.')

    def finish_clock_test(self, e, received, parts):
        self.expire_clock_tests()
        token = parts[1] if len(parts) > 1 else ''
        pending = self.clock_tests.get(token)
        error = None
        if not pending or pending.owner != nick_key(e.source.nick):
            error = 'ID unknown, expired, already used, or issued to another nickname. Request !timetest again.'
        elif (len(parts) != 4 or parts[2].lower() not in ('start', 'result')
              or not re.fullmatch(r'-?[0-9]{1,15}', parts[3])
              or not -62135596800000 <= int(parts[3]) <= 253402300799999):
            error = 'Use the generated mIRC snippet; callbacks need start/result and a Unix timestamp in milliseconds.'
        elif (parts[2].lower() == 'start' and pending.phase != 'issued'
              or parts[2].lower() == 'result' and pending.phase != 'sent'):
            error = 'This test is not awaiting that step. Request !timetest again if it timed out.'
        if error:
            if len(self.outbox) <= 50 and self.command_allowed(e.source.nick, 'clock-error'):
                self.say(self.channel, f'{e.source.nick}: {error}')
            return
        if parts[2].lower() == 'start':
            pending.client_send_us = int(parts[3]) * 1000
            pending.server_receive_us = epoch_us(received)
            pending.phase = 'queued'
            self.outbox.appendleft((self.channel, ClockProbe(token)))
            return
        del self.clock_tests[token]
        t1, t2, t3, t4 = (pending.client_send_us, pending.server_receive_us,
                          pending.server_send_us, int(parts[3]) * 1000)
        # Four timestamps exclude the bot's rate-limited reply queue from RTT.
        # Client clock offset is signed client-minus-bot, assuming symmetric paths.
        rtt_us = (t4 - t1) - (t3 - t2)
        # Whole millisecond client samples can under-report a sub-millisecond
        # loopback RTT by less than 1 ms. Larger negatives indicate a clock step.
        if rtt_us < -1000 or t3 < t2 or t4 < t1:
            self.say(self.channel, f'{e.source.nick}: Clock changed during the test or timestamps are inconsistent. '
                     'Request !timetest again.', priority=True)
            return
        rtt_us = max(0, rtt_us)
        offset_ms = ((t1 - t2) + (t4 - t3)) / 2000
        self.say(self.channel, f'{e.source.nick}: {(t1 - t2) / 1000:+.3f} ms total timing difference '
                 '(client - bot at receipt). Estimated Windows clock offset: '
                 f'{offset_ms:+.3f} ms (+ ahead / - behind). IRC RTT: '
                 f'{rtt_us / 1000:.3f} ms; one-way transit ~{rtt_us / 2000:.3f} ms. '
                 'Symmetric-path estimate; includes client handling.', priority=True)

    def send_help(self, e):
        self.say(self.channel, 'Commands: !help, !time / !leetbottime, !timetest, !topscore / !highscores, '
                 '!toptoday, !topweek, !topmonth, !topyear, !statistics.')
        self.say(self.channel, f'Type 1337 or leet during 13:37:00 <= time < 13:38:00 ({self.timezone}). '
                 'Target: 13:37:37.000000. Best attempt per period wins; retries are allowed.')
        self.send_stats_link(self.channel)

    def send_time(self, e, timestamp):
        self.say(self.channel, f'Server receipt time: {timestamp:%H:%M:%S.%f} ({self.timezone})', priority=True)

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
            self._scoreboard(self.channel, period, now, title)
        self.send_stats_link(self.channel)

    def send_top_scores(self, e, period):
        self._scoreboard(self.channel, period, self.now().date(), f'Top {period}')
        self.send_stats_link(self.channel)

    def send_statistics(self, e):
        rows = sorted(self.store.leaderboard('all', self.now().date()), key=lambda r: (-r['attempts'], -r['score']))
        self.say(self.channel, 'Lifetime statistics (top 10 by attendance):')
        for row in rows[:10]:
            avg = f"{row['average_score']:.2f}" if row['average_score'] is not None else 'unknown'
            self.say(self.channel, f"{row['nick']}: {row['attempts']} attempts, {row['days']} days, "
                     f"best {row['score']:.2f}, average {avg}")
        self.send_stats_link(self.channel)

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
                if isinstance(message, ClockProbe):
                    pending = self.clock_tests.get(message.token)
                    if not pending or pending.expires <= time.monotonic() or pending.phase != 'queued':
                        return
                    pending.server_send_us = epoch_us(self.now())
                    pending.phase = 'sent'
                    message = f'!leetclock {message.token} ready'
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
    bot = LeetBot(os.getenv('IRC_CHANNEL', '#mbhooden'), os.getenv('IRC_NICKNAME', 'LeetBot1337'),
                  os.getenv('IRC_SERVER', 'portlane.se.quakenet.org'), int(os.getenv('IRC_PORT', '6667')), store=store)
    app = create_app(store, password=password, clock_bot=bot)
    server = create_server(app, host=os.getenv('WEB_HOST', '0.0.0.0'), port=int(os.getenv('WEB_PORT', '8080')))
    worker = threading.Thread(target=server.run, name='stats-web', daemon=True)
    worker.start()
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
