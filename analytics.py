"""Dashboard aggregates with explicit definitions and no synthetic attendance."""
from collections import defaultdict
from datetime import date, timedelta
import json
import statistics


def dashboard(store, period, anchor, min_days=5):
    rows = store.attempts(period, anchor)
    leaders = store.leaderboard(period, anchor)
    all_leaders = store.leaderboard('all', anchor)
    by_day, by_player = (defaultdict(list) for _ in range(2))
    for row in rows:
        by_day[row['game_date']].append(row)
        by_player[row['player_id']].append(row)
    winners = []
    wins = defaultdict(int)
    for champion in store.calendar_winners('daily', period, anchor):
        day = champion['period']
        entries = by_day.get(day, [])
        for player in champion['player_ids']:
            wins[player] += 1
        winners.append({'date': day, 'nicks': champion['nicks'], 'score': champion['score'],
                        'participants': len({r['player_id'] for r in entries}), 'attempts': len(entries)})
    for row in leaders:
        entries = by_player[row['player_id']]
        offsets = [abs(r['offset_us']) / 1000 for r in entries if r['offset_us'] is not None]
        days = sorted({r['game_date'] for r in entries})
        longest = run = 0
        previous = None
        for day in days:
            current = date.fromisoformat(day)
            run = run + 1 if previous and current - previous == timedelta(days=1) else 1
            longest = max(longest, run)
            previous = current
        row.update(wins=wins[row['player_id']], longest_streak=longest,
                   median_offset_ms=statistics.median(offsets) if offsets else None,
                   deviation_ms=statistics.pstdev(offsets) if offsets else None)
    accuracy = sorted([r for r in leaders if r['days'] >= min_days and r['average_offset_ms'] is not None],
                      key=lambda r: (r['average_offset_ms'], -r['days'], r['nick']))
    offsets = [r['offset_us'] / 1000 for r in rows if r['offset_us'] is not None]
    closest = min((r for r in rows if r['offset_us'] is not None), key=lambda r: abs(r['offset_us']), default=None)
    histogram = [0] * 60
    for row in rows:
        if row['local_time'] and row['local_time'].startswith('13:37:'):
            histogram[int(row['local_time'][6:8])] += 1
    attendance = [{'date': day, 'players': len({r['player_id'] for r in entries}),
                   'attempts': len(entries), 'best': max(r['score'] for r in entries)}
                  for day, entries in sorted(by_day.items())]
    if attendance:
        first, last = date.fromisoformat(attendance[0]['date']), date.fromisoformat(attendance[-1]['date'])
        present = {r['date']: r for r in attendance}
        attendance = [present.get((first + timedelta(days=i)).isoformat(),
                                 {'date': (first + timedelta(days=i)).isoformat(), 'players': 0, 'attempts': 0, 'best': None})
                      for i in range((last - first).days + 1)]
    with store.connect() as db:
        years = [r[0] for r in db.execute('SELECT DISTINCT substr(game_date,1,4) FROM attempts ORDER BY 1 DESC')]
        last_date = db.execute('SELECT max(game_date) FROM attempts').fetchone()[0]
    meta = store.metadata()
    return {'period': period, 'anchor': anchor.isoformat(), 'timezone': store.timezone, 'years': years,
            'latest_date': last_date, 'min_days': min_days, 'leaderboard': leaders, 'accuracy': accuracy,
            'daily_winners': winners, 'monthly_winners': store.calendar_winners('monthly', period, anchor),
            'yearly_winners': store.calendar_winners('yearly', period, anchor), 'attendance': attendance,
            'histogram': histogram, 'recent': list(reversed(rows[-30:])),
            'import': json.loads(meta['legacy_import']) if 'legacy_import' in meta else None,
            'summary': {'attempts': len(rows), 'players': len(by_player), 'active_days': len(by_day),
                        'average_score': statistics.mean(r['score'] for r in rows) if rows else None,
                        'median_offset_ms': statistics.median(abs(n) for n in offsets) if offsets else None,
                        'early': sum(n < 0 for n in offsets), 'late': sum(n > 0 for n in offsets),
                        'exact': sum(n == 0 for n in offsets), 'within_100ms': sum(abs(n) <= 100 for n in offsets),
                        'closest': closest,
                        'all_time_high': all_leaders[0] if all_leaders else None}}
