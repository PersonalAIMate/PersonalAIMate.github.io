"""ScholarMate – minimal Flask web app."""
import json
import os
import sqlite3
import time

from flask import (Flask, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from arxiv_client import get_recommendations

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'scholarmate-dev-key-change-in-prod')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scholarmate.db')


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    print(f'[DB] Using database at: {DB_PATH}')
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    UNIQUE NOT NULL,
                password_hash TEXT    NOT NULL,
                scholar_url   TEXT    DEFAULT '',
                keywords      TEXT    DEFAULT '',
                top_k         INTEGER DEFAULT 10,
                cached_papers TEXT    DEFAULT '[]',
                cache_time    INTEGER DEFAULT 0
            )
        ''')
    print('[DB] init_db done')


def get_user(user_id):
    with get_db() as db:
        return db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        with get_db() as db:
            user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        error = 'Invalid email or password.'
    return render_template('login.html', error=error)


@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        if len(password) < 6:
            error = 'Password must be at least 6 characters.'
        else:
            try:
                with get_db() as db:
                    db.execute(
                        'INSERT INTO users (email, password_hash) VALUES (?, ?)',
                        (email, generate_password_hash(password))
                    )
                return redirect(url_for('login') + '?registered=1')
            except sqlite3.IntegrityError:
                error = 'This email is already registered.'
    return render_template('register.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    user   = get_user(session['user_id'])
    papers = json.loads(user['cached_papers'] or '[]')
    cache_age_min = (
        int((time.time() - user['cache_time']) / 60)
        if user['cache_time'] else None
    )
    return render_template('dashboard.html',
                           user=user,
                           papers=papers,
                           cache_age=cache_age_min)


@app.route('/settings', methods=['POST'])
@login_required
def settings():
    scholar_url = request.form.get('scholar_url', '').strip()
    keywords    = request.form.get('keywords',    '').strip()
    top_k       = max(1, min(50, int(request.form.get('top_k', 10) or 10)))
    with get_db() as db:
        db.execute(
            'UPDATE users SET scholar_url=?, keywords=?, top_k=? WHERE id=?',
            (scholar_url, keywords, top_k, session['user_id'])
        )
    print(f'[Settings] saved: scholar_url={scholar_url!r} keywords={keywords!r} top_k={top_k}')
    return redirect(url_for('dashboard'))


# ── Paper API ─────────────────────────────────────────────────────────────────

@app.route('/api/papers')
@login_required
def api_papers():
    user = get_user(session['user_id'])
    print(f'[API] scholar_url={user["scholar_url"]!r} keywords={user["keywords"]!r} top_k={user["top_k"]}')

    try:
        papers, used_keywords = get_recommendations(
            user['scholar_url'], user['keywords'], user['top_k']
        )
        if papers:
            with get_db() as db:
                db.execute(
                    'UPDATE users SET cached_papers=?, cache_time=? WHERE id=?',
                    (json.dumps(papers, ensure_ascii=False), int(time.time()), user['id'])
                )
        return jsonify(papers=papers, keywords=used_keywords)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(error=str(e)), 500


# ── Debug endpoint ────────────────────────────────────────────────────────────

@app.route('/api/debug')
@login_required
def api_debug():
    """Show current user settings from DB (helps diagnose issues)."""
    user = get_user(session['user_id'])
    return jsonify({
        'email':       user['email'],
        'scholar_url': user['scholar_url'],
        'keywords':    user['keywords'],
        'top_k':       user['top_k'],
        'cache_time':  user['cache_time'],
        'cached_count': len(json.loads(user['cached_papers'] or '[]')),
        'db_path':     DB_PATH,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=8019)
