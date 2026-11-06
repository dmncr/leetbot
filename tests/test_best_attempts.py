from datetime import date, datetime, timedelta

import pytest

from analytics import dashboard
from storage import ScoreStore


@pytest.fixture
def store(tmp_path):
    return ScoreStore(tmp_path / 'scores.sqlite3')


def test_individual_attempts_allow_repeated_players_and_rank_actual_times(store):
    target = datetime(2026, 1, 1, 13, 37, 37)
    store.add_attempt('repeat', 95, target + timedelta(microseconds=100))
    store.add_attempt('repeat', 90, target - timedelta(microseconds=200))
    # Historical scores may disagree with timing; timing determines this board.
    store.add_attempt('other', 100, target + timedelta(seconds=1))
    stats = dashboard(store, 'day', target.date())
    assert [r['nick'] for r in stats['best_attempts']] == ['repeat', 'repeat', 'other']
    assert [r['offset_us'] for r in stats['best_attempts']] == [100, -200, 1_000_000]
    assert [r['score'] for r in stats['best_attempts']] == [95, 90, 100]
    assert len(stats['leaderboard']) == 2


@pytest.mark.parametrize('period,expected', [
    ('day', ['today']), ('week', ['today', 'week']),
    ('month', ['today', 'week', 'month']), ('year', ['today', 'week', 'month', 'year']),
    ('all', ['today', 'week', 'month', 'year', 'older']),
])
def test_individual_attempts_obey_selected_period(store, period, expected):
    for nick, day, offset in [
        ('today', date(2026, 1, 15), 1), ('week', date(2026, 1, 12), 2),
        ('month', date(2026, 1, 1), 3), ('year', date(2026, 6, 1), 4),
        ('older', date(2025, 1, 15), 5),
    ]:
        store.add_attempt(nick, 99, datetime.combine(day, datetime.min.time()).replace(
            hour=13, minute=37, second=37, microsecond=offset))
    attempts = dashboard(store, period, date(2026, 1, 15))['best_attempts']
    assert [r['nick'] for r in attempts] == expected


def test_returns_best_twenty_from_full_history_in_stable_order(store):
    target = datetime(2026, 1, 1, 13, 37, 37)
    for offset in range(25, 0, -1):
        store.add_attempt('repeat', 99, target + timedelta(microseconds=offset))
    # These new poor attempts must not displace older personal bests.
    for offset in range(30, 65):
        store.add_attempt('recent', 98, target + timedelta(days=1, microseconds=offset))
    attempts = dashboard(store, 'all', target.date())['best_attempts']
    assert len(attempts) == 20
    assert [r['offset_us'] for r in attempts] == list(range(1, 21))


def test_equal_distances_keep_all_attempts_and_use_chronological_order(store):
    target = datetime(2026, 1, 1, 13, 37, 37)
    store.add_attempt('next-day', 99, target + timedelta(days=1, microseconds=100))
    store.add_attempt('late', 99, target + timedelta(microseconds=100))
    store.add_attempt('early', 99, target - timedelta(microseconds=100))
    attempts = dashboard(store, 'all', target.date())['best_attempts']
    assert [r['nick'] for r in attempts] == ['early', 'late', 'next-day']
    assert len({r['id'] for r in attempts}) == 3


def test_summary_only_records_do_not_become_individual_attempts(store, tmp_path):
    source = tmp_path / 'scores.json'
    source.write_text('{"daily":{"2026-1-1":{"summary":{"score":100,"timestamp":"13:37:37.000000"}}}}')
    store.migrate_json(source)
    stats = dashboard(store, 'all', date(2026, 1, 1))
    assert stats['best_attempts'] == []
    assert stats['leaderboard'][0]['nick'] == 'summary'
