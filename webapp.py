"""Password-gated statistics. No credentials or scores on public endpoints."""
from collections import OrderedDict, deque
from datetime import date, datetime, timedelta
from functools import wraps
import hashlib
import hmac
import os
import secrets
import threading
import time
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from analytics import dashboard


def create_app(store, *, password=None, secret_key=None):
    password = password if password is not None else os.environ.get('STATS_PASSWORD', '')
    if not password:
        raise ValueError('STATS_PASSWORD must be set')
    configured_secret = secret_key or os.getenv('SESSION_SECRET')
    if not configured_secret:
        with store.connect() as db:
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('session_secret',?)", (secrets.token_hex(32),))
            configured_secret = db.execute("SELECT value FROM metadata WHERE key='session_secret'").fetchone()[0]
    signing_key = hmac.new(configured_secret.encode(), password.encode(), hashlib.sha256).hexdigest()
    password_hash = hashlib.sha256(password.encode()).digest()
    app = Flask(__name__)
    app.config.update(SECRET_KEY=signing_key, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=4096)
    attempts, rate_lock = OrderedDict(), threading.Lock()
    cache, cache_lock = {}, threading.Lock()

    def protected(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get('authenticated'):
                if request.path.startswith('/api/'):
                    return jsonify(error='Authentication required'), 401
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return wrapper

    def csrf():
        token = session.get('csrf')
        supplied = request.form.get('csrf', '')
        if not token or not hmac.compare_digest(token.encode(), supplied.encode()):
            abort(400, 'Invalid form token. Reload and try again.')

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"})
        return response

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        error, status = None, 200
        if request.method == 'POST':
            csrf()
            peer, now = request.remote_addr or 'unknown', time.monotonic()
            with rate_lock:
                recent = attempts.setdefault(peer, deque())
                attempts.move_to_end(peer)
                while recent and now - recent[0] >= 300:
                    recent.popleft()
                blocked = len(recent) >= 10
                if not blocked:
                    recent.append(now)
                while len(attempts) > 4096:
                    attempts.popitem(last=False)
            if blocked:
                error, status = 'Too many attempts. Try again in five minutes.', 429
            elif hmac.compare_digest(hashlib.sha256(request.form.get('password', '').encode()).digest(), password_hash):
                session.clear()
                session.update(authenticated=True, csrf=secrets.token_urlsafe(32))
                session.permanent = True
                with rate_lock:
                    attempts.pop(peer, None)
                return redirect(url_for('index'))
            else:
                error, status = 'Incorrect password. Try again.', 401
        return render_template('login.html', error=error), status

    @app.post('/logout')
    @protected
    def logout():
        csrf()
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @protected
    def index():
        return render_template('dashboard.html')

    @app.get('/api/stats')
    @protected
    def stats():
        try:
            period = request.args.get('period', 'all')
            if period not in ('day', 'week', 'month', 'year', 'all'):
                raise ValueError('Invalid period')
            anchor = date.fromisoformat(request.args.get('date') or datetime.now(ZoneInfo(store.timezone)).date().isoformat())
            if not 2 <= anchor.year <= 9998:
                raise ValueError('Date outside supported range')
            min_days = int(request.args.get('min_days', '5'))
            if not 1 <= min_days <= 365:
                raise ValueError('Minimum days must be 1–365')
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        key = (period, anchor, min_days)
        with cache_lock:
            item = cache.get(key)
            if item and time.monotonic() - item[0] < 15:
                return jsonify(item[1])
        result = dashboard(store, period, anchor, min_days)
        with cache_lock:
            if len(cache) >= 32:
                cache.clear()
            cache[key] = (time.monotonic(), result)
        return jsonify(result)

    @app.get('/healthz')
    def health():
        try:
            with store.connect() as db:
                db.execute('SELECT 1 FROM attempts LIMIT 1')
            return jsonify(status='ok')
        except Exception:
            app.logger.exception('Database health check failed')
            return jsonify(status='unhealthy'), 503

    return app
