from datetime import date, datetime, timedelta
import socket
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import irc.client
import pytest

from game import calculate_score
from leetbot import LeetBot, SafeBuffer
from storage import ScoreStore


@pytest.fixture
def bot(tmp_path):
    return LeetBot('#game','bot','localhost',store=ScoreStore(tmp_path / 'scores.sqlite3'),
                   stats_url='https://stats.example.org',stats_password='channel-password')


def event(message, nick='player', target='#game'):
    return SimpleNamespace(arguments=[message],source=SimpleNamespace(nick=nick),target=target)


@pytest.mark.parametrize('text',['1337','leet','I33T','a 1337 b'])
def test_leet_variants(text):
    assert LeetBot.is_leet_message(text)


@pytest.mark.parametrize('text',['13370','xleet','not a match'])
def test_non_leet(text):
    assert not LeetBot.is_leet_message(text)


def test_exact_target():
    assert calculate_score(datetime(2026,1,1,13,37,37)) == 100
    assert calculate_score(datetime(2026,1,1,13,37,38)) == pytest.approx(100*(1-1/13))
    assert calculate_score(datetime(2026,1,1,13,37,24)) == 1
    assert calculate_score(datetime(2026,1,1,13,37,23)) == 1


@pytest.mark.parametrize('stamp,count',[('13:36:59.999999',0),('13:37:00',1),('13:37:37',1),('13:37:59.999999',1),('13:38:00',0)])
def test_competition_boundaries(bot,stamp,count):
    bot.now = lambda: datetime.fromisoformat('2026-01-01T'+stamp).replace(tzinfo=ZoneInfo('Europe/Stockholm'))
    c = Mock(); c.get_nickname.return_value = 'bot'
    bot.on_pubmsg(c,event('1337'))
    assert len(bot.store.attempts('day',date(2026,1,1))) == count


def test_own_messages_and_other_channels_ignored(bot):
    bot.now = lambda: datetime(2026,1,1,13,37,37)
    c = Mock(); c.get_nickname.return_value = 'bot'
    bot.on_pubmsg(c,event('1337',nick='BOT'))
    bot.on_pubmsg(c,event('1337',target='#other'))
    assert not bot.store.attempts('day',date(2026,1,1))


def test_required_outputs_include_url_and_password(bot):
    for action in [lambda:bot.send_help(event('!help')),lambda:bot.send_highscores(event('!topscore')),
                   lambda:bot.send_top_scores(event('!topweek'),'week'),
                   lambda:bot.make_announcements(datetime(2026,10,4,13,38,30))]:
        bot.outbox.clear(); action()
        assert any('https://stats.example.org' in text and 'channel-password' in text for _,text in bot.outbox)


def test_line_splitting_encoding_and_injection(bot):
    bot.say('#game','ø'*1000+'\r\nPRIVMSG #other :bad')
    assert len(bot.outbox) > 1
    assert all(len(text.encode()) < 380 and '\r' not in text and '\n' not in text for _,text in bot.outbox)
    buffer = SafeBuffer(); buffer.feed(b'bad\xff\r\n')
    assert list(buffer.lines()) == ['bad\ufffd']


def test_announcements_once_and_january_rollover(bot):
    stamp = datetime(2026,1,1,13,38,30)
    bot.now = lambda: stamp
    bot.joined = True
    bot.connection.is_connected = lambda: True
    bot.connection.privmsg = Mock()
    bot.make_announcements = Mock(wraps=bot.make_announcements)
    bot.store.add_attempt('old',99,datetime(2025,12,31,13,37,37))
    bot.tick(); bot.tick()
    assert bot.make_announcements.call_count == 1
    assert any('old' in text for _,text in bot.outbox)


def test_disconnect_clears_stale_queue(bot):
    bot.say('#game','stale')
    bot.joined = True
    bot.on_disconnect(bot.connection,None)
    assert not bot.joined and not bot.outbox


@pytest.mark.parametrize('private_replies', [False, True])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('command', ['!help', '!time', '!leetbottime', '!timetest', '!topscore', '!topscores',
                                     '!highscores', '!toptoday', '!topweek', '!topmonth', '!topyear', '!statistics'])
def test_command_reply_routing(bot, private_replies, private, command):
    bot.private_replies = private_replies
    bot.now = lambda: datetime(2026, 1, 1, 12, tzinfo=bot.timezone)
    c = Mock(); c.get_nickname.return_value = 'bot'
    handler = bot.on_privmsg if private else bot.on_pubmsg
    handler(c, event(command, target='bot' if private else '#game'))
    assert bot.outbox
    expected = 'player' if private_replies and command not in ('!time', '!leetbottime') else '#game'
    assert all(target == expected for target, _ in bot.outbox)


@pytest.mark.parametrize('value,expected', [(None, False), ('false', False), ('0', False), ('off', False),
                                         ('true', True), (' TRUE ', True), ('1', True), ('yes', True), ('on', True)])
def test_private_replies_configuration(bot, monkeypatch, value, expected):
    monkeypatch.delenv('PRIVATE_REPLIES', raising=False)
    if value is not None:
        monkeypatch.setenv('PRIVATE_REPLIES', value)
    configured = LeetBot('#game', 'bot', 'localhost', store=bot.store)
    assert configured.private_replies is expected
    overridden = LeetBot('#game', 'bot', 'localhost', store=bot.store, private_replies=not expected)
    assert overridden.private_replies is not expected


def test_scheduled_announcements_remain_public_in_private_mode(bot):
    bot.private_replies = True
    bot.make_announcements(datetime(2026, 10, 4, 13, 38, 30))
    assert bot.outbox and all(target == '#game' for target, _ in bot.outbox)


def test_cooldown_is_per_player_and_command_family(bot):
    bot.now = lambda: datetime(2026, 1, 1, 12, tzinfo=bot.timezone)
    c = Mock(); c.get_nickname.return_value = 'bot'
    bot.on_pubmsg(c, event('!help'))
    bot.on_pubmsg(c, event('!leetbottime'))
    assert 'Server receipt time' in bot.outbox[0][1]
    count = len(bot.outbox)
    bot.on_pubmsg(c, event('!time'))
    assert len(bot.outbox) == count  # An alias cannot bypass the same-command cooldown.
    bot.on_pubmsg(c, event('!leetbottime', nick='another-player'))
    assert len(bot.outbox) == count + 1


def request_clock_test(bot, nick='player'):
    c = Mock(); c.get_nickname.return_value = 'bot'
    bot.on_pubmsg(c, event('!timetest', nick=nick))
    token = next(reversed(bot.clock_tests))
    assert any(f'/timetest/{token}' in text for _, text in bot.outbox)
    return c, token


def send_probe(bot, send_time):
    bot.now = lambda: send_time
    bot.joined = True
    bot.connection.is_connected = lambda: True
    bot.connection.privmsg = Mock()
    bot.tick()
    bot.connection.privmsg.assert_called_once()
    return bot.connection.privmsg.call_args.args[1]


@pytest.mark.parametrize('offset_ms,expected', [(37, '+37.000'), (-42, '-42.000')])
@pytest.mark.parametrize('private_replies', [False, True])
def test_clock_round_trip_offset_and_queue_exclusion(bot, offset_ms, expected, private_replies):
    bot.private_replies = private_replies
    received = datetime(2026, 1, 1, 13, 37, 37, 250000, tzinfo=bot.timezone)
    bot.now = lambda: received
    c, token = request_clock_test(bot)
    t1 = int(received.timestamp() * 1000) + offset_ms - 25
    bot.on_pubmsg(c, event(f'!timetest {token} start {t1}'))
    assert bot.clock_tests[token].phase == 'queued'
    # Five seconds waiting for IRC output must not become network latency.
    sent = received + timedelta(seconds=5)
    assert send_probe(bot, sent) == f'!leetclock {token} ready'
    assert bot.connection.privmsg.call_args.args[0] == ('player' if private_replies else '#game')
    t4 = int(sent.timestamp() * 1000) + offset_ms + 25
    bot.on_pubmsg(c, event(f'!timetest {token} result {t4}'))
    assert f'Estimated Windows clock offset: {expected} ms' in bot.outbox[0][1]
    assert bot.outbox[0][1].startswith(f'player: {offset_ms - 25:+.3f} ms total timing difference')
    assert 'IRC RTT: 50.000 ms; one-way transit ~25.000 ms' in bot.outbox[0][1]
    assert token not in bot.clock_tests
    assert all(target == ('player' if private_replies else '#game') for target, _ in bot.outbox)
    assert not bot.store.attempts('all', date(2026, 1, 1))
    bot.outbox.clear()
    bot.on_pubmsg(c, event(f'!timetest {token} result {t4}'))
    assert 'already used' in bot.outbox[0][1]
    assert bot.outbox[0][0] == ('player' if private_replies else '#game')


def test_clock_id_bound_to_nickname_and_private_callback(bot):
    received = datetime(2026, 1, 1, 12, tzinfo=bot.timezone)
    bot.now = lambda: received
    c, token = request_clock_test(bot, nick='Player[')
    stamp = int(received.timestamp() * 1000)
    bot.on_pubmsg(c, event(f'!timetest {token} start {stamp}', nick='intruder'))
    assert bot.clock_tests[token].phase == 'issued'
    bot.on_pubmsg(c, event(f'!timetest {token} start $ctime', nick='Player['))
    assert bot.clock_tests[token].phase == 'issued'
    assert any('Unix timestamp in milliseconds' in text for _, text in bot.outbox)
    bot.on_privmsg(c, event(f'!timetest {token} start {stamp}', nick='PLAYER{', target='bot'))
    send_probe(bot, received)
    bot.on_privmsg(c, event(f'!timetest {token} result {stamp}', nick='PLAYER{', target='bot'))
    assert token not in bot.clock_tests
    assert 'Estimated Windows clock offset: +0.000 ms' in bot.outbox[0][1]


@pytest.mark.parametrize('stamp', ['nan', 'inf', '999999999999999999999', '-62135596800001', '253402300800000', '123 extra'])
def test_clock_callback_rejects_invalid_timestamps(bot, stamp):
    c, token = request_clock_test(bot)
    bot.on_pubmsg(c, event(f'!timetest {token} start {stamp}'))
    assert bot.clock_tests[token].phase == 'issued'
    assert not any('Estimated Windows clock offset' in text for _, text in bot.outbox)


def test_clock_rejects_wrong_step_and_duplicate_start(bot):
    c, token = request_clock_test(bot)
    stamp = int(bot.now().timestamp() * 1000)
    bot.on_pubmsg(c, event(f'!timetest {token} result {stamp}'))
    assert bot.clock_tests[token].phase == 'issued'
    bot.on_pubmsg(c, event(f'!timetest {token} start {stamp}'))
    count = len(bot.outbox)
    bot.on_pubmsg(c, event(f'!timetest {token} start {stamp}'))
    assert len(bot.outbox) == count
    assert bot.clock_tests[token].phase == 'queued'


def test_clock_rejects_negative_rtt(bot):
    received = datetime(2026, 1, 1, 12, tzinfo=bot.timezone)
    bot.now = lambda: received
    c, token = request_clock_test(bot)
    stamp = int(received.timestamp() * 1000)
    bot.on_pubmsg(c, event(f'!timetest {token} start {stamp}'))
    send_probe(bot, received + timedelta(seconds=1))
    bot.on_pubmsg(c, event(f'!timetest {token} result {stamp + 500}'))
    assert token not in bot.clock_tests
    assert 'Clock changed' in bot.outbox[0][1]


def test_clock_millisecond_rounding_on_fast_link(bot):
    received = datetime(2026, 1, 1, 12, tzinfo=bot.timezone)
    bot.now = lambda: received
    c, token = request_clock_test(bot)
    stamp = int(received.timestamp() * 1000)
    bot.on_pubmsg(c, event(f'!timetest {token} start {stamp}'))
    send_probe(bot, received + timedelta(seconds=1, microseconds=500))
    bot.on_pubmsg(c, event(f'!timetest {token} result {stamp + 1000}'))
    assert 'IRC RTT: 0.000 ms' in bot.outbox[0][1]
    assert 'Estimated Windows clock offset: -0.250 ms' in bot.outbox[0][1]


def test_clock_expiry_new_id_and_disconnect(bot, monkeypatch):
    monotonic = [1000.0]
    monkeypatch.setattr('leetbot.time.monotonic', lambda: monotonic[0])
    c, first = request_clock_test(bot)
    monotonic[0] += 6
    _, second = request_clock_test(bot)
    assert first not in bot.clock_tests and second != first
    monotonic[0] += 300
    bot.on_pubmsg(c, event(f'!timetest {second} start 1767271057000'))
    assert not bot.clock_tests
    assert any('expired' in text for _, text in bot.outbox)
    monotonic[0] += 6
    request_clock_test(bot)
    bot.on_disconnect(c, None)
    assert not bot.clock_tests


def test_private_attempts_never_score_and_timing_replies_keep_pacing(bot):
    bot.now = lambda: datetime(2026, 1, 1, 13, 37, 37, tzinfo=bot.timezone)
    c = Mock(); c.get_nickname.return_value = 'bot'
    bot.on_privmsg(c, event('1337', target='bot'))
    assert not bot.store.attempts('all', date(2026, 1, 1))
    bot.say('#game', 'queued scoreboard')
    bot.on_pubmsg(c, event('!leetbottime'))
    bot.joined = True
    bot.connection.is_connected = lambda: True
    bot.connection.privmsg = Mock()
    bot.tick()
    bot.connection.privmsg.assert_called_once()
    assert 'Server receipt time' in bot.connection.privmsg.call_args.args[1]
    assert len(bot.outbox) == 1


@pytest.mark.parametrize('private_replies', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_reusable_clock_negotiates_fresh_id_on_each_run(bot, monkeypatch, private, private_replies):
    bot.private_replies = private_replies
    target = 'player' if private_replies else '#game'
    monotonic = [1000.0]
    monkeypatch.setattr('leetbot.time.monotonic', lambda: monotonic[0])
    received = datetime(2026, 1, 1, 13, 37, 37, tzinfo=bot.timezone)
    c = Mock(); c.get_nickname.return_value = 'bot'
    handler = bot.on_privmsg if private else bot.on_pubmsg
    previous = None
    for request in ('123456789012', '987654321098'):
        bot.now = lambda: received
        bot.outbox.clear()
        handler(c, event(f'!timetest request {request}', target='bot' if private else '#game'))
        token = next(iter(bot.clock_tests))
        assert token != previous
        assert bot.outbox[0] == (target, f'!leetclock {request} issued player {token}')
        assert bot.clock_tests[token].owner == 'player'
        # Startup/negotiation precedes T1; output queue waiting must not affect RTT.
        t1 = int(received.timestamp() * 1000) - 10
        handler(c, event(f'!timetest {token} start {t1}'))
        sent = received + timedelta(seconds=3)
        assert send_probe(bot, sent) == f'!leetclock {token} ready'
        assert bot.connection.privmsg.call_args.args[0] == target
        t4 = int(sent.timestamp() * 1000) + 30
        handler(c, event(f'!timetest {token} result {t4}'))
        assert 'IRC RTT: 40.000 ms' in bot.outbox[0][1]
        assert 'Windows clock offset: +10.000 ms' in bot.outbox[0][1]
        assert all(dest == target for dest, _ in bot.outbox)
        assert not bot.clock_tests
        previous = token
        monotonic[0] += 6
    assert not bot.store.attempts('all', received.date())


@pytest.mark.parametrize('private_replies', [False, True])
def test_reusable_clock_cooldown_concurrent_players_and_owner_binding(bot, private_replies):
    bot.private_replies = private_replies
    bot.now = lambda: datetime(2026, 1, 1, 13, 37, 37, tzinfo=bot.timezone)
    c = Mock(); c.get_nickname.return_value = 'bot'
    request = '123456789012'
    bot.on_pubmsg(c, event(f'!timetest request {request}', nick='alice'))
    alice = next(iter(bot.clock_tests))
    bot.on_pubmsg(c, event(f'!timetest request {request}', nick='alice'))
    assert len(bot.clock_tests) == 1 and alice in bot.clock_tests
    assert bot.outbox[0][1].startswith(f'!leetclock {request} error')
    assert bot.outbox[0][0] == ('alice' if private_replies else '#game')
    bot.on_pubmsg(c, event('!timetest', nick='alice'))
    assert len(bot.clock_tests) == 1  # Human and script requests share a cooldown.
    bot.on_pubmsg(c, event(f'!timetest request {request}', nick='bob'))
    assert len(bot.clock_tests) == 2
    assert bot.outbox[0][1].startswith(f'!leetclock {request} issued bob ')
    assert bot.outbox[0][0] == ('bob' if private_replies else '#game')
    bot.on_pubmsg(c, event(f'!timetest {alice} start 1767271057000', nick='bob'))
    assert bot.clock_tests[alice].phase == 'issued'
    assert bot.outbox[-1][0] == ('bob' if private_replies else '#game')


@pytest.mark.parametrize('payload', ['request', 'request x', 'request 123456789012 extra',
                                   'request 12345678901g', 'request ' + 'a' * 500])
def test_reusable_clock_rejects_malformed_negotiation(bot, payload):
    bot.now = lambda: datetime(2026, 1, 1, 13, 37, 37, tzinfo=bot.timezone)
    c = Mock(); c.get_nickname.return_value = 'bot'
    bot.on_pubmsg(c, event('!timetest ' + payload))
    assert not bot.clock_tests and not bot.outbox
    assert not bot.store.attempts('all', bot.now().date())


@pytest.mark.parametrize('private_replies', [False, True])
def test_real_irc_protocol_handshake_scoring_and_reply(tmp_path, private_replies):
    """Loopback-only IRC exchange verifies framework handlers and buffer wiring."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        listener.listen(1)
        listener.settimeout(2)
        bot = LeetBot('#game','bot','127.0.0.1',listener.getsockname()[1],
                      store=ScoreStore(tmp_path / 'protocol.sqlite3'),
                      stats_url='https://stats.example.org',stats_password='protocol-password',
                      private_replies=private_replies)
        target = 'player' if private_replies else '#game'
        bot.now = lambda: datetime(2026,1,1,13,37,37,tzinfo=ZoneInfo('Europe/Stockholm'))
        bot._connect()
        with listener.accept()[0] as peer:
            peer.settimeout(2)
            received = b''
            while b'USER ' not in received or not received.endswith(b'\r\n'):
                received += peer.recv(4096)
            assert b'NICK bot' in received
            peer.sendall(b':server 001 bot :Welcome\r\n')
            bot.reactor.process_once(.1)
            assert b'JOIN #game' in peer.recv(4096)
            peer.sendall(b':bot!u@h JOIN #game\r\n'
                         b':player!u@h PRIVMSG #game :invalid \xff\r\n'
                         b':player!u@h PRIVMSG #game :1337\r\n')
            bot.reactor.process_once(.1)
            assert bot.joined and isinstance(bot.connection.buffer,SafeBuffer)
            assert bot.store.leaderboard('day',date(2026,1,1))[0]['score'] == 100
            bot.outbox.clear()
            peer.sendall(b':player!u@h PRIVMSG #game :!topscore\r\n')
            bot.reactor.process_once(.1)
            while bot.outbox:
                bot.tick()
            reply = b''
            while b'protocol-password' not in reply:
                reply += peer.recv(4096)
            assert f'PRIVMSG {target} :Stats: https://stats.example.org'.encode() in reply
            # Private commands must use the same public reply path.
            peer.sendall(b':player!u@h PRIVMSG bot :!leetbottime\r\n')
            bot.reactor.process_once(.1)
            bot.tick()
            assert b'PRIVMSG #game :Server receipt time:' in peer.recv(4096)
            peer.sendall(b':player!u@h PRIVMSG #game :!timetest\r\n')
            bot.reactor.process_once(.1)
            token = next(iter(bot.clock_tests))
            while bot.outbox:
                bot.tick()
            assert f'/timetest/{token}'.encode() in peer.recv(4096)
            received_time = bot.now()
            t1 = int(received_time.timestamp() * 1000) - 18
            peer.sendall(f':player!u@h PRIVMSG #game :!timetest {token} start {t1}\r\n'.encode())
            bot.reactor.process_once(.1)
            sent_time = received_time + timedelta(seconds=4)
            bot.now = lambda: sent_time
            bot.tick()
            assert f'PRIVMSG {target} :!leetclock {token} ready'.encode() in peer.recv(4096)
            t4 = int(sent_time.timestamp() * 1000) + 42
            peer.sendall(f':player!u@h PRIVMSG #game :!timetest {token} result {t4}\r\n'.encode())
            bot.reactor.process_once(.1)
            bot.tick()
            response = peer.recv(4096)
            assert f'PRIVMSG {target} :player: -18.000 ms total timing difference'.encode() in response
            assert b'Estimated Windows clock offset: +12.000 ms' in response
            assert b'IRC RTT: 60.000 ms; one-way transit ~30.000 ms' in response
            # The reusable client negotiates through a normal IRC message too.
            bot.command_times.clear()
            request_target = 'bot' if private_replies else '#game'
            peer.sendall(f':player!u@h PRIVMSG {request_target} :!timetest request 123456789012\r\n'.encode())
            bot.reactor.process_once(.1)
            bot.tick()
            token = next(iter(bot.clock_tests))
            assert f'PRIVMSG {target} :!leetclock 123456789012 issued player {token}'.encode() in peer.recv(4096)
            bot.connection.disconnect('test complete')
