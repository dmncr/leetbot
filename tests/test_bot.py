from datetime import date, datetime
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


def test_real_irc_protocol_handshake_scoring_and_reply(tmp_path):
    """Loopback-only IRC exchange verifies framework handlers and buffer wiring."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        listener.listen(1)
        listener.settimeout(2)
        bot = LeetBot('#game','bot','127.0.0.1',listener.getsockname()[1],
                      store=ScoreStore(tmp_path / 'protocol.sqlite3'),
                      stats_url='https://stats.example.org',stats_password='protocol-password')
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
            assert b'PRIVMSG #game :Stats: https://stats.example.org' in reply
            bot.connection.disconnect('test complete')
