import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("SURFACE_DATA_DIR", ROOT / "data")).resolve()


def initialize():
    for name in ("originals", "output", "profiles", "temp"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, relative_name TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE, stored_path TEXT NOT NULL,
            width INTEGER NOT NULL, height INTEGER NOT NULL, dates TEXT NOT NULL,
            chosen_date TEXT NOT NULL, date_source TEXT NOT NULL, warning TEXT,
            status TEXT NOT NULL DEFAULT 'pending', draft TEXT, imported_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS labels (
            id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL UNIQUE REFERENCES images(id),
            score INTEGER NOT NULL CHECK(score BETWEEN 10 AND 100),
            coarse INTEGER NOT NULL, crop TEXT NOT NULL, captured_at TEXT NOT NULL,
            date_source TEXT NOT NULL, output_path TEXT NOT NULL, model_id TEXT NOT NULL,
            prediction_id INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL REFERENCES images(id),
            model_id TEXT NOT NULL, crop TEXT NOT NULL, criteria TEXT NOT NULL,
            score INTEGER, uncertainty REAL, result TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS features (
            label_id INTEGER NOT NULL REFERENCES labels(id), model_id TEXT NOT NULL,
            vector TEXT NOT NULL, PRIMARY KEY(label_id, model_id));
        CREATE INDEX IF NOT EXISTS image_status ON images(status);
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            scope TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL);
        ''')
        from .portable import relocate_if_needed
        relocate_if_needed(ROOT, db)


@contextmanager
def connection():
    db = sqlite3.connect(str(DATA / "surface.sqlite3"), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def setting(key, default=None):
    with connection() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key, value):
    with connection() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))


def image_row(image_id):
    with connection() as db:
        row = db.execute("SELECT * FROM images WHERE id=?", (int(image_id),)).fetchone()
    if not row:
        raise ValueError("Bild wurde nicht gefunden.")
    return dict(row)


def label_dataset(rows=None):
    """Model-independent identity of the saved human labels, including crop and score."""
    if rows is None:
        with connection() as db:
            rows = db.execute('''SELECT l.id,l.score,l.crop FROM labels l JOIN images i ON i.id=l.image_id
                                 WHERE i.status='rated' ORDER BY l.id''').fetchall()
    labels = [[r["id"], r["score"], json.loads(r["crop"])] for r in rows]
    signature = hashlib.sha256(json.dumps(labels, separators=(",", ":")).encode()).hexdigest()
    return {"count": len(labels), "signature": signature}
