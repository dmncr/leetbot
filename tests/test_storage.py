from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import pytest

from analytics import dashboard
from storage import ScoreStore


@pytest.fixture
def store(tmp_path):
    return ScoreStore(tmp_path / 'scores.sqlite3')


def legacy(tmp_path, data):
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def test_live_file_is_lossless_and_import_is_idempotent(store):
    path = Path(__file__).resolve().parents[1] / 'scores.json'
    if not path.exists():
        pytest.skip('Private live scores fixture is not checked into the repository')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    original = json.loads(path.read_bytes())
    expected = [entry for key, values in original['daily'].items() if key.endswith('_contestants') for entry in values]
    result = store.migrate_json(path)
    assert result['attempts'] == len(expected)
    actual = store.attempts('all', date(2026, 10, 6))
    assert len(actual) == len(expected)
    # Every score and timestamp survives, including inconsistent historical scores.
    assert sorted((r['nick'],r['local_time'],r['score']) for r in actual) == sorted(
        (r['nick'],datetime.strptime(r['timestamp'], '%H:%M:%S.%f').strftime('%H:%M:%S.%f'),r['score']) for r in expected)
    for period, values in original.items():
        for key, players in values.items():
            if key.endswith('_contestants'):
                continue
            if period == 'weekly':
                year, week = map(int, key.split('-W'))
                anchor = date.fromisocalendar(year, week, 1)
            elif period == 'monthly':
                anchor = date(*map(int,key.split('-')),1)
            elif period == 'yearly':
                anchor = date(int(key),1,1)
            else:
                anchor = date(*map(int,key.split('-')))
            board = {r['nick']:r['score'] for r in store.leaderboard(period, anchor)}
            for nick, value in players.items():
                assert board[nick] >= (value['score'] if isinstance(value,dict) else value)
    assert store.migrate_json(path) is False
    assert len(store.attempts('all', date.today())) == len(expected)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_migration_rollback_and_retry(store, tmp_path):
    entries = [{'nick':'a','timestamp':'13:37:37.000000','score':100},
               {'nick':'b','timestamp':'invalid','score':90}]
    source = legacy(tmp_path, {'daily': {'2026-1-1_contestants': entries}})
    with pytest.raises(ValueError):
        store.migrate_json(source)
    assert not store.attempts('all', date.today())
    assert 'legacy_import' not in store.metadata()
    entries.pop()
    source = legacy(tmp_path, {'daily': {'2026-1-1_contestants': entries}})
    assert store.migrate_json(source)['attempts'] == 1


def test_summaries_do_not_invent_attendance(store, tmp_path):
    source = legacy(tmp_path, {'daily': {'2026-1-1': {'a':100}},
        'monthly': {'2026-1': {'a':100}}, 'yearly': {'2026': {'b':99}}})
    assert store.migrate_json(source)['daily_summaries'] == 1
    stats = dashboard(store, 'all', date(2026,1,1))
    assert stats['summary']['attempts'] == 0
    assert stats['summary']['closest'] is None
    assert stats['leaderboard'][0]['score'] == 100
    assert stats['leaderboard'][0]['attempts'] == 0
    assert stats['daily_winners'][0]['nicks'] == ['a']
    assert stats['yearly_winners'][0]['nicks'] == ['a']


def test_iso_week_and_calendar_years(store):
    for day, score in [('2024-12-30',95),('2025-01-01',96),('2026-01-01',99)]:
        store.add_attempt('a',score,datetime.fromisoformat(day+'T13:37:37'))
    row = store.leaderboard('week',date(2025,1,1))[0]
    assert row['attempts'] == 2 and row['score'] == 96
    row = store.leaderboard('month',date(2025,1,1))[0]
    assert row['attempts'] == 1 and row['score'] == 96
    assert store.leaderboard('year',date(2024,1,1))[0]['score'] == 95


def test_concurrent_readers_and_writers(store):
    def work(i):
        store.add_attempt('a',i,datetime(2026,1,1,13,37,37,i))
        return store.leaderboard('day',date(2026,1,1))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work,range(40)))
    assert all(r for r in results)
    assert store.leaderboard('day',date(2026,1,1))[0]['attempts'] == 40


def test_simultaneous_start_imports_once(store, tmp_path):
    source = legacy(tmp_path, {'daily': {'2026-1-1_contestants': [{'nick':'a','score':100,'timestamp':'13:37:37'}]}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.migrate_json(source),range(4)))
    assert sum(bool(r) for r in results) == 1
    assert len(store.attempts('all',date.today())) == 1


def test_nick_casefold_and_best_attempt(store):
    store.add_attempt('Player[',95,datetime(2026,1,1,13,37,37))
    store.add_attempt('PLAYER{',90,datetime(2026,1,1,13,37,38))
    board = store.leaderboard('day',date(2026,1,1))
    assert len(board) == 1 and board[0]['score'] == 95 and board[0]['attempts'] == 2


def test_database_timezone_cannot_change(store):
    with pytest.raises(ValueError, match='reinterpret history'):
        ScoreStore(store.path,'America/New_York')


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,101,True,'100'])
def test_invalid_score_rejected(store,value):
    with pytest.raises(ValueError):
        store.add_attempt('a',value,datetime(2026,1,1))


def test_ties_and_accuracy_sample_threshold(store):
    for day in range(1,6):
        store.add_attempt('a',100,datetime(2026,1,day,13,37,37,1000))
        store.add_attempt('b',100,datetime(2026,1,day,13,37,37,2000))
    store.add_attempt('c',100,datetime(2026,1,1,13,37,37))
    stats = dashboard(store,'month',date(2026,1,1),min_days=5)
    assert stats['accuracy'][0]['nick'] == 'a'
    assert [r['nick'] for r in stats['accuracy']] == ['a','b']
    assert set(stats['daily_winners'][-1]['nicks']) == {'a','b','c'}
    assert all(r['wins'] == 5 for r in stats['leaderboard'] if r['nick'] != 'c')
