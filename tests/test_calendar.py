from datetime import date, datetime
import pytest

from game import calculate_score
from storage import ScoreStore


def test_champions_use_full_calendar_period(tmp_path):
    store = ScoreStore(tmp_path / 'scores.sqlite3')
    store.add_attempt('early',90,datetime(2026,1,1,13,37,37))
    store.add_attempt('later',99,datetime(2026,1,31,13,37,37))
    assert store.calendar_winners('monthly','day',date(2026,1,1))[0]['nicks'] == ['later']
    assert store.calendar_winners('yearly','day',date(2026,1,1))[0]['nicks'] == ['later']
    assert store.calendar_winners('daily','day',date(2026,1,1))[0]['nicks'] == ['early']


def test_score_is_monotonic_with_distance():
    scores = [calculate_score(datetime(2026,1,1,13,37,37 + i//1000,i%1000*1000)) for i in range(22000)]
    assert scores == sorted(scores,reverse=True)
    assert min(scores) == 1 and max(scores) == 100
