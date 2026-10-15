from datetime import datetime

import pytest

from storage import ScoreStore
from webapp import create_app


@pytest.fixture
def app(tmp_path):
    store = ScoreStore(tmp_path / 'scores.sqlite3')
    store.add_attempt('<script>alert(1)</script>',99,datetime(2026,1,1,13,37,37))
    app = create_app(store,password='test-password')
    app.config['TESTING'] = True
    return app


def token(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf']


def login(client, password='test-password'):
    return client.post('/login',data={'csrf':token(client),'password':password})


def test_authentication_and_logout(app):
    client = app.test_client()
    assert client.get('/').status_code == 302
    assert client.get('/api/stats').status_code == 401
    assert 'test-password' not in client.get('/login').text
    assert login(client,'wrong').status_code == 401
    response = login(client)
    assert response.status_code == 302
    cookie = response.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    assert client.get('/').status_code == 200
    stats = client.get('/api/stats').json
    assert stats['summary']['attempts'] == 1
    assert client.post('/logout').status_code == 400
    with client.session_transaction() as session:
        csrf = session['csrf']
    assert client.post('/logout',data={'csrf':csrf}).status_code == 302
    assert client.get('/api/stats').status_code == 401


def test_csrf_and_throttle(app):
    client = app.test_client()
    assert client.post('/login',data={'password':'test-password'}).status_code == 400
    assert client.post('/login',data={'csrf':'ø','password':'test-password'}).status_code == 400
    csrf = token(client)
    for _ in range(10):
        assert client.post('/login',data={'csrf':csrf,'password':'bad'}).status_code == 401
    assert client.post('/login',data={'csrf':csrf,'password':'test-password'},headers={'X-Forwarded-For':'other'}).status_code == 429


@pytest.mark.parametrize('query',['period=invalid','date=invalid','date=9999-01-01','min_days=0','min_days=366','min_days=nan'])
def test_invalid_query_is_400(app,query):
    client = app.test_client(); login(client)
    assert client.get('/api/stats?'+query).status_code == 400


def test_empty_period(app):
    client = app.test_client(); login(client)
    data = client.get('/api/stats?period=day&date=2026-02-01').json
    assert data['leaderboard'] == [] and data['attendance'] == []
    assert data['summary']['closest'] is None
    assert data['summary']['all_time_high']['score'] == 99


def test_overview_and_history_are_protected_and_split(app):
    client = app.test_client()
    assert client.get('/history').status_code == 302
    login(client)
    overview = client.get('/').text
    history = client.get('/history').text
    assert 'When did everyone hit send?' in overview
    assert '13:37:36.500–13:37:37.500' in overview
    assert 'id="leaderboard-body"' in overview and 'id="leaderboard-body"' in history
    assert 'id="timing-split"' in overview and 'id="heatmap"' in overview
    assert 'id="accuracy-body"' not in overview and 'id="winners-body"' not in overview
    assert 'id="attempts-body"' not in overview
    assert 'id="accuracy-body"' in history and 'id="winners-body"' in history
    assert 'id="attempts-body"' in history and 'id="timing-chart"' in history
    assert client.get('/api/stats').json['timing_window']['included'] == 1


def test_clock_snippet_authentication_expiry_and_started_id(tmp_path):
    from types import SimpleNamespace
    from leetbot import LeetBot

    store = ScoreStore(tmp_path / 'clock.sqlite3')
    bot = LeetBot('#game', 'bot', 'localhost', store=store)
    bot.connection.real_nickname = 'bot'  # Simulate the registered bot without connecting.
    bot.start_clock_test(SimpleNamespace(source=SimpleNamespace(nick='player')))
    token = next(iter(bot.clock_tests))
    app = create_app(store, password='test-password', clock_bot=bot)
    client = app.test_client()
    assert client.get(f'/timetest/{token}').status_code == 302
    assert login(client).headers['Location'] == f'/timetest/{token}'
    page = client.get(f'/timetest/{token}')
    assert page.status_code == 200 and 'Copy mIRC script' in page.text
    script = client.get(f'/timetest/{token}/script.mrc')
    assert script.status_code == 200 and script.mimetype == 'text/plain'
    assert f'leetbot-clock-{token}.mrc' in script.headers['Content-Disposition']
    assert f'msg #game !timetest {token} start' in script.text
    assert f'on *:TEXT:!leetclock {token} *:#game:' in script.text
    assert 'ToUnixTimeMilliseconds' in script.text
    assert 'timer' in script.text and 'Terminate' in script.text
    assert client.get('/timetest/unknown').status_code == 404
    bot.clock_tests[token].phase = 'queued'
    assert client.get(f'/timetest/{token}').status_code == 404
    bot.clock_tests[token].phase = 'issued'
    bot.clock_tests[token].expires = 0
    assert client.get(f'/timetest/{token}/script.mrc').status_code == 404


def test_headers_and_public_health(app):
    response = app.test_client().get('/healthz')
    assert response.json == {'status':'ok'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert "script-src 'self'" in response.headers['Content-Security-Policy']


def test_session_survives_restart_and_password_rotation_invalidates_it(tmp_path):
    store = ScoreStore(tmp_path / 'sessions.sqlite3')
    app = create_app(store,password='test-password')
    client = app.test_client(); login(client)
    old_cookie = client.get_cookie('session').value
    restarted = create_app(store,password='test-password').test_client()
    restarted.set_cookie('session',old_cookie)
    assert restarted.get('/api/stats').status_code == 200
    rotated = create_app(store,password='new-password').test_client()
    rotated.set_cookie('session',old_cookie)
    assert rotated.get('/api/stats').status_code == 401
