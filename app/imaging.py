import hashlib
import io
import json
import math
import re
import shutil
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .storage import DATA, connection, setting

EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def now():
    return datetime.now().replace(microsecond=0).isoformat()


def opened(path):
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def normalize_date(value):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    if not 1900 <= parsed.year <= 2200:
        raise ValueError("Datum liegt außerhalb des unterstützten Bereichs.")
    return parsed.replace(microsecond=0).isoformat()


def date_info(path):
    stat = path.stat()
    dates = {"import": now(), "file_mtime_ns": stat.st_mtime_ns, "file_ctime_ns": stat.st_ctime_ns,
             "modified": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
             "created": datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime)).replace(microsecond=0).isoformat()}
    with Image.open(path) as img:
        exif = img.getexif()
        try:
            exif = {**dict(exif), **dict(exif.get_ifd(34665))}
        except (KeyError, AttributeError, TypeError):
            exif = dict(exif)
        for key, tag in (("exif_original", 36867), ("exif_digitized", 36868), ("exif_datetime", 306)):
            raw = str(exif.get(tag, "")).strip("\x00 ")
            if raw:
                dates[key + "_raw"] = raw
                try:
                    dates[key] = datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
        for name, tag in (("exif_offset", 36880), ("exif_offset_original", 36881),
                          ("exif_offset_digitized", 36882), ("exif_subseconds", 37520),
                          ("exif_subseconds_original", 37521), ("exif_subseconds_digitized", 37522)):
            if exif.get(tag):
                dates[name] = str(exif[tag])
    warnings, selected = [], None
    for key in ("exif_original", "exif_digitized", "exif_datetime", "modified", "created", "import"):
        if key not in dates:
            if dates.get(key + "_raw"):
                warnings.append(key + ": nicht lesbar")
            continue
        dt = datetime.fromisoformat(dates[key])
        if dt < datetime(1990, 1, 1) or dt > datetime.now() + timedelta(days=1):
            warnings.append(key + ": unplausibel (" + dates[key] + ")")
        elif selected is None:
            selected = key
    selected = selected or "import"
    if selected not in ("exif_original", "exif_digitized", "exif_datetime"):
        warnings.append("Kein plausibles EXIF-Datum; Dateidatum bzw. Importdatum vorgeschlagen.")
    return dates, selected, "; ".join(warnings)


def import_folder(folder, recursive=True, progress=lambda text: None):
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Bitte einen vorhandenen Bilderordner auswählen.")
    if root == DATA or DATA in root.parents:
        raise ValueError("Der interne Datenordner kann nicht importiert werden.")
    export_folder = Path(setting("output", str(DATA / "output"))).resolve()
    if root == export_folder or export_folder in root.parents:
        raise ValueError("Der Ausgabeordner kann nicht als Bilderquelle importiert werden.")
    paths = sorted(root.rglob("*") if recursive else root.iterdir())
    imported, duplicates, errors = 0, 0, []
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS or DATA in path.resolve().parents or export_folder in path.resolve().parents:
            continue
        try:
            progress("Import: " + path.name)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            fingerprint = hashlib.sha256((str(path.resolve()) + digest.hexdigest()).encode()).hexdigest()
            with connection() as db:
                if db.execute("SELECT id FROM images WHERE fingerprint=?", (fingerprint,)).fetchone():
                    duplicates += 1
                    continue
            dates, source, warning = date_info(path)
            img = opened(path)
            target = DATA / "originals" / (fingerprint + path.suffix.lower())
            shutil.copy2(path, target)
            with connection() as db:
                db.execute('''INSERT INTO images (source,relative_name,fingerprint,stored_path,width,height,
                           dates,chosen_date,date_source,warning,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                           (str(path), str(path.relative_to(root)), fingerprint, str(target), *img.size,
                            json.dumps(dates), dates[source], source, warning, now()))
            imported += 1
        except Exception as exc:
            errors.append({"file": str(path), "error": str(exc)})
    return {"imported": imported, "duplicates": duplicates, "errors": errors}


def checked_crop(crop, width, height):
    if not isinstance(crop, list) or len(crop) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in crop):
        raise ValueError("Bildausschnitt muss aus vier gültigen Koordinaten bestehen.")
    box = [round(v) for v in crop]
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("Der Bildausschnitt liegt außerhalb des Bildes oder ist leer.")
    return box


def crop_image(row, crop):
    return opened(row["stored_path"]).crop(checked_crop(crop, row["width"], row["height"]))


def jpeg_bytes(img, max_size=None):
    if max_size:
        img.thumbnail((max_size, max_size))
    stream = io.BytesIO()
    img.save(stream, "JPEG", quality=94)
    return stream.getvalue()


def filename(date, original, score):
    stem = unicodedata.normalize("NFKD", Path(original).stem).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_.")[:70] or "Bild"
    return datetime.fromisoformat(date).strftime("%Y-%m-%d_%H-%M-%S") + f"_{stem}_{score}.jpg"


def texture_features(image):
    """Deterministic descriptor, explicitly not a pretrained vision encoder."""
    rgb = np.asarray(image.resize((64, 64), Image.Resampling.LANCZOS), dtype=np.float32) / 255
    gray = rgb.mean(axis=2)
    items = []
    for channel in range(3):
        values = rgb[:, :, channel]
        items.extend(np.histogram(values, bins=16, range=(0, 1))[0] / values.size)
        items.extend([values.mean(), values.std()])
    for y in range(0, 64, 16):
        for x in range(0, 64, 16):
            patch = gray[y:y + 16, x:x + 16]
            items.extend([patch.mean(), patch.std(), np.abs(np.diff(patch, axis=0)).mean(), np.abs(np.diff(patch, axis=1)).mean()])
    items.extend(np.histogram(np.abs(np.diff(gray, axis=0)), bins=16, range=(0, 1))[0] / (63 * 64))
    return np.array(items, dtype=np.float64)
