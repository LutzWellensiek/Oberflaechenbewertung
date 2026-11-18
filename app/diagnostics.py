"""Local error history; no telemetry or credentials."""
import json
import re
import traceback
from .storage import connection


def redact(text):
    text = re.sub(r"hf_[A-Za-z0-9]{12,}", "[Token entfernt]", str(text))
    return re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[Token entfernt]", text)


def record_error(scope, message, details=""):
    try:
        with connection() as db:
            cursor = db.execute("INSERT INTO errors (scope,message,details) VALUES (?,?,?)",
                                (str(scope)[:200], redact(message)[:6000], redact(details)[:20000]))
            return cursor.lastrowid
    except Exception:
        traceback.print_exc()
        return None


def recent_errors(limit=30):
    with connection() as db:
        return [dict(r) for r in db.execute("SELECT * FROM errors ORDER BY id DESC LIMIT ?", (limit,))]
