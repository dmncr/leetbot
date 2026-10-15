from datetime import date, datetime, timedelta

from analytics import dashboard
from storage import ScoreStore


def test_timing_window_boundaries_and_provenance(tmp_path):
    store = ScoreStore(tmp_path / 'timing.sqlite3')
    target = datetime(2026, 1, 1, 13, 37, 37)
    offsets = [-500001, -500000, -475001, -475000, -1, 0,
               24999, 25000, 499999, 500000, 500001]
    for offset in offsets:
        store.add_attempt('player', 99, target + timedelta(microseconds=offset))
    store.add_attempt('other-day', 99, target + timedelta(days=1))
    with store.connect() as db:
        player = db.execute('SELECT id FROM players WHERE nick=?', ('player',)).fetchone()[0]
        db.execute('''INSERT INTO attempts (player_id,game_date,score,source)
                      VALUES (?, '2026-01-01', 90, 'legacy')''', (player,))
        db.execute('''INSERT INTO attempts (player_id,game_date,offset_us,score,source)
                      VALUES (?, '2026-01-01', 0, 100, 'legacy_summary')''', (player,))
    data = dashboard(store, 'day', date(2026, 1, 1))
    window = data['timing_window']
    assert window['start_us'] == -500000 and window['end_us'] == 500000
    assert window['bin_us'] == 25000 and len(window['bins']) == 40
    assert window['included'] == sum(window['bins']) == 9
    assert window['outside'] == 2 and window['unknown'] == 1
    assert window['bins'][0] == 2 and window['bins'][1] == 1
    assert window['bins'][19] == 1 and window['bins'][20] == 2
    assert window['bins'][21] == 1 and window['bins'][39] == 2
    summary = data['summary']
    assert summary['early'] == 5 and summary['late'] == 5 and summary['exact'] == 1
    assert window['included'] + window['outside'] + window['unknown'] == summary['attempts']
    assert summary['early'] + summary['late'] + summary['exact'] == 11


def test_empty_timing_window(tmp_path):
    store = ScoreStore(tmp_path / 'empty.sqlite3')
    window = dashboard(store, 'all', date(2026, 1, 1))['timing_window']
    assert window['bins'] == [0] * 40
    assert window['included'] == window['outside'] == window['unknown'] == 0
