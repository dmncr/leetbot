"""Shared competition rules. All competition dates are local to GAME_TIMEZONE."""
from datetime import date, timedelta
import re

LEET = re.compile(r'\b[1il][3e]{2}[7t]\b', re.IGNORECASE)


def timing(timestamp):
    target = timestamp.replace(hour=13, minute=37, second=37, microsecond=0)
    return round((timestamp - target).total_seconds() * 1_000_000)


def calculate_score(timestamp):
    difference = abs(timing(timestamp)) / 1_000_000
    # Linear falloff with a monotonic consolation floor (the old 13s cliff gave 0).
    return max(1.0, 100 * (1 - difference / 13))


def period_bounds(period, anchor):
    if period in ('day', 'today', 'daily'):
        start, end = anchor, anchor + timedelta(days=1)
    elif period in ('week', 'weekly'):
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
    elif period in ('month', 'monthly'):
        start = anchor.replace(day=1)
        end = date(anchor.year + (anchor.month == 12), anchor.month % 12 + 1, 1)
    elif period in ('year', 'yearly'):
        start, end = date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1)
    elif period == 'all':
        return '0001-01-01', '9999-12-31'
    else:
        raise ValueError('Unknown period')
    return start.isoformat(), end.isoformat()


def period_key(period, anchor):
    if period == 'daily':
        return anchor.isoformat()
    if period == 'weekly':
        year, week, _ = anchor.isocalendar()
        return f'{year}-W{week:02}'
    if period == 'monthly':
        return anchor.strftime('%Y-%m')
    return str(anchor.year)


def nick_key(nick):
    """RFC1459 case folding; unrelated nicknames remain separate players."""
    return nick.lower().translate(str.maketrans('[]\\^', '{}|~'))
