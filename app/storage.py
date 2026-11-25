import hashlib
import json
import os
import sqlite3
from datetime import datetime
from contextlib import closing, contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("SURFACE_DATA_DIR", ROOT / "data")).resolve()


def initialize():
    for name in ("originals", "output", "profiles", "temp", "history", "backups"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    with connection(foreign_keys=False) as db:
        previous = db.execute("SELECT sql FROM sqlite_master WHERE name='labels'").fetchone()
        if previous and "revision" not in previous[0]:
            backup = DATA / "backups" / ("before-workspace-v2-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3")
            with closing(sqlite3.connect(backup)) as destination:
                db.backup(destination)
            db.executescript('''
                CREATE TABLE labels_v2 (
                    id INTEGER PRIMARY KEY,image_id INTEGER NOT NULL UNIQUE REFERENCES images(id),
                    score INTEGER NOT NULL CHECK(score=0 OR score BETWEEN 10 AND 100),coarse INTEGER NOT NULL,
                    crop TEXT NOT NULL,captured_at TEXT NOT NULL,date_source TEXT NOT NULL,output_path TEXT NOT NULL,
                    model_id TEXT NOT NULL,prediction_id INTEGER,created_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,alternatives TEXT NOT NULL DEFAULT '[]',
                    provenance TEXT NOT NULL DEFAULT 'human',training_enabled INTEGER NOT NULL DEFAULT 1);
                INSERT INTO labels_v2(id,image_id,score,coarse,crop,captured_at,date_source,output_path,model_id,prediction_id,created_at)
                    SELECT id,image_id,score,coarse,crop,captured_at,date_source,output_path,model_id,prediction_id,created_at FROM labels;
                DROP TABLE labels;
                ALTER TABLE labels_v2 RENAME TO labels;
            ''')
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
            score INTEGER NOT NULL CHECK(score=0 OR score BETWEEN 10 AND 100),
            coarse INTEGER NOT NULL, crop TEXT NOT NULL, captured_at TEXT NOT NULL,
            date_source TEXT NOT NULL, output_path TEXT NOT NULL, model_id TEXT NOT NULL,
            prediction_id INTEGER, created_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,alternatives TEXT NOT NULL DEFAULT '[]',
            provenance TEXT NOT NULL DEFAULT 'human',training_enabled INTEGER NOT NULL DEFAULT 1);
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
        CREATE TABLE IF NOT EXISTS label_history (
            id INTEGER PRIMARY KEY,image_id INTEGER NOT NULL,action TEXT NOT NULL,
            snapshot TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS model_runs (
            id TEXT PRIMARY KEY,name TEXT NOT NULL,base_model TEXT NOT NULL,task TEXT NOT NULL,
            temporal INTEGER NOT NULL,recipe TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS model_results (
            run_id TEXT NOT NULL REFERENCES model_runs(id),image_id INTEGER NOT NULL REFERENCES images(id),
            label_revision INTEGER NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL,
            PRIMARY KEY(run_id,image_id));
        ''')
        columns = {r[1] for r in db.execute("PRAGMA table_info(images)")}
        for name, definition in (("origin_folder", "TEXT NOT NULL DEFAULT ''"),
                                 ("previous_status", "TEXT"), ("relevance", "INTEGER")):
            if name not in columns:
                db.execute(f"ALTER TABLE images ADD COLUMN {name} {definition}")
        for row in db.execute("SELECT id,source,relative_name FROM images WHERE origin_folder='' ").fetchall():
            source, relative = Path(row["source"]), Path(row["relative_name"])
            parent = source
            for _ in relative.parts:
                parent = parent.parent
            folder = str(Path(parent.name) / relative.parent) if parent.name else str(relative.parent)
            db.execute("UPDATE images SET origin_folder=? WHERE id=?", (folder, row["id"]))
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Die Datenbankmigration hat ungültige Verknüpfungen gefunden.")
        from .portable import relocate_if_needed
        relocate_if_needed(ROOT, db)


@contextmanager
def connection(foreign_keys=True):
    db = sqlite3.connect(str(DATA / "surface.sqlite3"), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=" + ("ON" if foreign_keys else "OFF"))
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
            rows = db.execute('''SELECT l.*,i.origin_folder FROM labels l JOIN images i ON i.id=l.image_id
                                 WHERE i.status='rated' AND l.score BETWEEN 10 AND 99 AND l.training_enabled=1 AND l.provenance!='ai' ORDER BY l.id''').fetchall()
    labels = [[r["id"], r["score"], json.loads(r["crop"]),
               *[r[key] if key in r.keys() else None for key in ("revision", "alternatives", "captured_at", "origin_folder")]] for r in rows]
    signature = hashlib.sha256(json.dumps(labels, separators=(",", ":")).encode()).hexdigest()
    return {"count": len(labels), "signature": signature}
