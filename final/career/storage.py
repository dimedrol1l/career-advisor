"""Atomic JSON snapshots in local SQLite, with optimistic concurrency control."""
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException
from career.schemas import Application

DEFAULT_DB = Path(__file__).resolve().parents[1] / 'data' / 'career.sqlite3'


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    path = Path(os.getenv('SBER21_DB', str(DEFAULT_DB)))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        os.chmod(path, 0o600)
        db.execute('CREATE TABLE IF NOT EXISTS applications (id TEXT PRIMARY KEY, version INTEGER, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS versions (application_id TEXT, version INTEGER, body TEXT NOT NULL, PRIMARY KEY(application_id,version))')
        db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, application_id TEXT, stage TEXT, status TEXT, updated_at TEXT)')
        yield db


def get(application_id):
    with connection() as db:
        row = db.execute('SELECT body FROM applications WHERE id=?', (application_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Отклик не найден.')
    return Application.model_validate_json(row[0])


def all_applications():
    with connection() as db:
        rows = db.execute('SELECT body FROM applications').fetchall()
    apps = [Application.model_validate_json(row[0]) for row in rows]
    return sorted(apps, key=lambda a: a.updated_at, reverse=True)


def save(app, expected_version=None):
    old_version = app.version
    app.updated_at = now()
    if not app.created_at:
        app.created_at = app.updated_at
    app.version += 1
    body = app.model_dump_json()
    with connection() as db:
        if expected_version is None:
            db.execute('INSERT INTO applications VALUES (?,?,?)', (app.id, app.version, body))
        else:
            cursor = db.execute('UPDATE applications SET version=?,body=? WHERE id=? AND version=?',
                                (app.version, body, app.id, expected_version))
            if cursor.rowcount != 1:
                app.version = old_version
                raise HTTPException(409, 'Отклик изменился в другой вкладке. Обновите страницу перед повтором.')
        db.execute('INSERT INTO versions VALUES (?,?,?)', (app.id, app.version, body))
    return app


def delete(application_id):
    with connection() as db:
        db.execute('DELETE FROM versions WHERE application_id=?', (application_id,))
        db.execute('DELETE FROM runs WHERE application_id=?', (application_id,))
        cursor = db.execute('DELETE FROM applications WHERE id=?', (application_id,))
    if not cursor.rowcount:
        raise HTTPException(404, 'Отклик не найден.')


def versions(application_id):
    get(application_id)
    with connection() as db:
        rows = db.execute('SELECT version,body FROM versions WHERE application_id=? ORDER BY version DESC', (application_id,)).fetchall()
    return [{'version': v, 'updated_at': Application.model_validate_json(body).updated_at} for v, body in rows]


def restore(application_id, version, expected_version):
    current = get(application_id)
    with connection() as db:
        row = db.execute('SELECT body FROM versions WHERE application_id=? AND version=?', (application_id, version)).fetchone()
    if not row:
        raise HTTPException(404, 'Версия не найдена.')
    app = Application.model_validate_json(row[0])
    app.version = current.version
    return save(app, expected_version)


def record_run(run_id, application_id, stage, status):
    with connection() as db:
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at',
                   (run_id, application_id, stage, status, now()))


def runs(application_id):
    with connection() as db:
        rows = db.execute('SELECT stage,status,updated_at FROM runs WHERE application_id=? ORDER BY updated_at DESC LIMIT 30', (application_id,)).fetchall()
    return [{'stage': stage, 'status': status, 'updated_at': date} for stage, status, date in rows]


def interrupt_runs():
    with connection() as db:
        db.execute("UPDATE runs SET status='interrupted',updated_at=? WHERE status='running'", (now(),))
