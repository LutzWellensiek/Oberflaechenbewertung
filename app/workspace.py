"""Human labels, reversible image lifecycle, grouped exports and the library."""
import hashlib
import json
from pathlib import Path
import random
import re
import threading
import uuid

from .imaging import checked_crop, crop_image, filename, jpeg_bytes, normalize_date, now
from .storage import DATA, connection, image_row, setting

SAVE_LOCK = threading.RLock()


def validate_score(value):
    if isinstance(value, bool) or not isinstance(value, int) or not (value == 0 or 10 <= value <= 99):
        raise ValueError("Bewertung: 0 = nicht bewertbar oder eine ganze Zahl von 10 bis 99.")
    return value


def export_directory(row):
    base = Path(setting("output", str(DATA / "output"))).expanduser().resolve()
    if base == DATA or base == DATA / "originals" or DATA / "originals" in base.parents:
        raise ValueError("Bitte einen separaten Ausgabeordner wählen.")
    # Keep folder labels, including non-ASCII names, without permitting traversal.
    parts = []
    for part in row.get("origin_folder", "").replace("\\", "/").split("/"):
        part = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part).strip(" .")
        if part and part not in (".", ".."):
            if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
                part = "_" + part
            parts.append(part[:100])
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Ungültige Ordnerbeschriftung.")
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_export(row, crop, captured, score, ai=False):
    directory = export_directory(row)
    name = filename(captured, row["relative_name"], score)
    if ai:
        name = Path(name).stem + "_KI.jpg"
    target, suffix = directory / name, 1
    payload = jpeg_bytes(crop_image(row, crop))
    while True:
        try:
            with target.open("xb") as stream:
                stream.write(payload)
            return target
        except FileExistsError:
            target = directory / (Path(name).stem + f"_{suffix}.jpg")
            suffix += 1


def history(db, image_id, action, label):
    db.execute("INSERT INTO label_history(image_id,action,snapshot,created_at) VALUES (?,?,?,?)",
               (image_id, action, json.dumps(dict(label) if label else {}, ensure_ascii=False), now()))


def save_human_label(body):
    with SAVE_LOCK:
        row = image_row(body["image_id"])
        if row["status"] != "pending":
            raise ValueError("Zum Ändern zuerst 'Neu bewerten' wählen.")
        score = validate_score(body.get("score"))
        alternatives = body.get("alternatives", [])
        if not isinstance(alternatives, list) or len(alternatives) > 3:
            raise ValueError("Bitte höchstens drei alternative Bewertungen angeben.")
        alternatives = sorted({validate_score(v) for v in alternatives if v != score})
        if 0 in alternatives or score == 0 and alternatives:
            raise ValueError("Alternative Qualitätsstufen müssen zwischen 10 und 99 liegen.")
        crop = checked_crop(body["crop"], row["width"], row["height"])
        captured = normalize_date(body["date"])
        source = str(body.get("date_source", "manual"))
        if source != "manual" and json.loads(row["dates"]).get(source) != captured:
            raise ValueError("Datumsquelle passt nicht zum Datum. Für eine Korrektur 'Manuell' wählen.")
        target = write_export(row, crop, captured, score)
        try:
            with connection() as db:
                old = db.execute("SELECT * FROM labels WHERE image_id=?", (row["id"],)).fetchone()
                expected = body.get("expected_revision", old["revision"] if old else 0)
                if expected != (old["revision"] if old else 0):
                    raise ValueError("Die Bewertung wurde inzwischen geändert. Bitte neu laden.")
                history(db, row["id"], "revised" if old else "created", old)
                revision = old["revision"] + 1 if old else 1
                values = (score, score // 10 * 10, json.dumps(crop), captured, source, str(target),
                          "manual", now(), revision, json.dumps(alternatives), row["id"])
                if old:
                    db.execute("DELETE FROM features WHERE label_id=?", (old["id"],))
                    db.execute('''UPDATE labels SET score=?,coarse=?,crop=?,captured_at=?,date_source=?,output_path=?,
                        model_id=?,prediction_id=NULL,created_at=?,revision=?,alternatives=?,provenance='human' WHERE image_id=?''', values)
                else:
                    db.execute('''INSERT INTO labels(score,coarse,crop,captured_at,date_source,output_path,model_id,
                        created_at,revision,alternatives,image_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', values)
                db.execute("UPDATE labels SET training_enabled=? WHERE image_id=?",
                           (int(bool(body.get("training_enabled", old["training_enabled"] if old else True))), row["id"]))
                db.execute("UPDATE images SET status='rated',chosen_date=?,date_source=?,draft=NULL,previous_status=NULL WHERE id=?",
                           (captured, source, row["id"]))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return {"saved": str(target), "revision": revision, "provenance": "human"}


def change_image(body):
    with SAVE_LOCK:
        row = image_row(body["image_id"])
        action = body["action"]
        with connection() as db:
            label = db.execute("SELECT * FROM labels WHERE image_id=?", (row["id"],)).fetchone()
            if action == "training":
                if not label:
                    raise ValueError("Nur bewertete Bilder können für Training freigegeben werden.")
                db.execute("UPDATE labels SET training_enabled=? WHERE image_id=?", (int(bool(body["enabled"])), row["id"]))
                return {"ok": True}
            if action == "relevance":
                value = body.get("value")
                if value is not None and (type(value) is not int or value not in (0, 1)):
                    raise ValueError("Passend: 1, unpassend: 0, unbekannt: leer.")
                db.execute("UPDATE images SET relevance=? WHERE id=?", (value, row["id"]))
                return {"ok": True}
            if action not in ("reopen", "ignore", "remove", "restore"):
                raise ValueError("Unbekannte Bildaktion.")
            if action == "restore":
                if row["status"] not in ("removed", "ignored"):
                    raise ValueError("Dieses Bild ist bereits aktiv.")
                status = row["previous_status"] or ("rated" if label else "pending")
            else:
                status = {"reopen": "pending", "ignore": "ignored", "remove": "removed"}[action]
                if row["status"] == "removed" or action == "reopen" and row["status"] != "rated":
                    raise ValueError("Bitte das Bild zuerst wiederherstellen.")
                if status == row["status"]:
                    return {"ok": True, "status": status}
            history(db, row["id"], action, label)
            moved = None
            created = None
            try:
                if label and row["status"] == "rated" and status != "rated":
                    old = Path(label["output_path"])
                    if old.is_file():
                        archive = DATA / "history" / (uuid.uuid4().hex + "_" + old.name)
                        old.replace(archive)
                        moved = (old, archive)
                        db.execute("UPDATE labels SET output_path=? WHERE image_id=?", (str(archive), row["id"]))
                elif label and status == "rated" and row["status"] != "rated":
                    created = write_export(row, json.loads(label["crop"]), label["captured_at"], label["score"])
                    db.execute("UPDATE labels SET output_path=? WHERE image_id=?", (str(created), row["id"]))
                previous = ("rated" if label else "pending") if action == "restore" and status == "ignored" else None if action == "restore" else row["status"]
                db.execute("UPDATE images SET status=?,previous_status=?,draft=NULL WHERE id=?", (status, previous, row["id"]))
            except BaseException:
                if moved:
                    moved[1].replace(moved[0])
                if created:
                    created.unlink(missing_ok=True)
                raise
        return {"ok": True, "status": status}


def rebuild_exports(progress=lambda message: None):
    count = 0
    with SAVE_LOCK:
        with connection() as db:
            rows = [dict(r) for r in db.execute('''SELECT i.*,l.id label_id,l.score,l.crop,l.captured_at,l.output_path
                FROM images i JOIN labels l ON l.image_id=i.id WHERE i.status='rated' ORDER BY i.id''')]
        for index, row in enumerate(rows):
            progress(f"Ausgabe nach Herkunft ablegen: {index + 1}/{len(rows)}")
            old = Path(row["output_path"])
            expected = filename(row["captured_at"], row["relative_name"], row["score"])
            matching_name = re.fullmatch(re.escape(Path(expected).stem) + r"(?:_\d+)?\.jpg", old.name)
            if old.is_file() and old.parent.resolve() == export_directory(row) and matching_name:
                continue
            new = write_export(row, json.loads(row["crop"]), row["captured_at"], row["score"])
            archive = None
            try:
                with connection() as db:
                    if old.is_file():
                        archive = DATA / "history" / (uuid.uuid4().hex + "_" + old.name)
                        old.replace(archive)
                    history(db, row["id"], "reexport", {"output_path": str(archive or old), "score": row["score"]})
                    db.execute("UPDATE labels SET output_path=? WHERE id=?", (str(new), row["label_id"]))
            except BaseException:
                new.unlink(missing_ok=True)
                if archive:
                    archive.replace(old)
                raise
            count += 1
    return {"updated": count, "total": len(rows)}


def list_images(query):
    def value(key, default=""):
        return query.get(key, [default])[0]
    status = value("status", "pending")
    if status not in ("all", "pending", "rated", "ignored", "removed"):
        raise ValueError("Ungültiger Bildstatus.")
    with connection() as db:
        rows = [dict(r) for r in db.execute('''SELECT i.id,i.relative_name,i.origin_folder,i.width,i.height,i.status,
            i.relevance,i.chosen_date,i.draft,l.score,l.crop,l.captured_at,l.revision,l.alternatives,l.provenance,l.training_enabled
            FROM images i LEFT JOIN labels l ON l.image_id=i.id ORDER BY i.id''')]
        run_results = {r["image_id"]: dict(r) for r in db.execute("SELECT * FROM model_results WHERE run_id=?", (value("run_id"),))}
    from .model_lab import result_is_current, run_is_stale
    stale = run_is_stale(value("run_id")) if value("run_id") else False
    for row in rows:
        row.update(ai_score=None, gap=None, result=None, result_stale=False)
        result = run_results.get(row["id"])
        if result:
            data = json.loads(result["result"])
            row["result"] = data
            row["result_stale"] = stale or result["label_revision"] != (row["revision"] or 0) or not result_is_current(row, data, data.get("temporal"))
            if not row["result_stale"]:
                row["ai_score"] = data.get("score")
                if row["score"] not in (None, 0) and row["ai_score"] is not None:
                    row["gap"] = abs(row["score"] - row["ai_score"])
    filtered = []
    for row in rows:
        if (status == "all" and row["status"] == "removed") or status != "all" and row["status"] != status:
            continue
        if value("search").casefold() not in (row["relative_name"] + " " + row["origin_folder"]).casefold():
            continue
        if value("folder") and row["origin_folder"] != value("folder"):
            continue
        if value("min_score") and (row["score"] is None or row["score"] < int(value("min_score"))):
            continue
        if value("max_score") and (row["score"] is None or row["score"] > int(value("max_score"))):
            continue
        if value("min_gap") and (row["gap"] is None or row["gap"] < float(value("min_gap"))):
            continue
        if value("training") and bool(row["training_enabled"]) != (value("training") == "yes"):
            continue
        if value("relevance") and str(row["relevance"]) != value("relevance"):
            continue
        if value("ai_relevance"):
            if row["result_stale"] or not row["result"] or row["result"].get("relevant") is None or int(row["result"]["relevant"]) != int(value("ai_relevance")):
                continue
        filtered.append(row)
    order = value("order", "random")
    if order == "random":
        seed = value("seed", "surface")
        filtered.sort(key=lambda r: hashlib.sha256(f"{seed}:{r['id']}".encode()).digest())
    elif order == "gap":
        filtered.sort(key=lambda r: (r["gap"] is None, -(r["gap"] or 0), r["id"]))
    elif order == "score":
        filtered.sort(key=lambda r: (r["score"] is None, r["score"] or 0, r["id"]))
    elif order == "date":
        filtered.sort(key=lambda r: (r["chosen_date"], r["id"]))
    return filtered
