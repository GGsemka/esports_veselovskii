"""Validate content, remove file metadata and discard uploads on rollback."""

import io
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename


def remove_upload(filename):
    """Remove only app-owned uploads. Static artwork may also be stored as a banner."""
    if filename and re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", filename):
        try:
            (Path(current_app.config["UPLOAD_FOLDER"]) / filename).unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception("Unable to remove unused upload %s", filename)


@contextmanager
def uploaded(file):
    if not file or not file.filename:
        yield None
        return
    ext = secure_filename(Path(file.filename).suffix.lstrip(".")).lower()
    types = {"jpeg": "JPEG", "jpg": "JPEG", "png": "PNG", "webp": "WEBP"}
    if ext not in types:
        raise ValueError("Допустимы файлы JPG, PNG и WebP.")
    data = file.stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Размер изображения должен быть не более 1 МБ.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format != types[ext] or img.width * img.height > 12_000_000:
                raise ValueError("Формат или разрешение изображения недопустимо.")
            img.load()
            clean = img.convert("RGB" if types[ext] == "JPEG" else "RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать изображение.") from exc
    name = f"{uuid4().hex}.{ext}"
    path = Path(current_app.config["UPLOAD_FOLDER"]) / name
    try:
        clean.save(path, format=types[ext])
        yield name
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        clean.close()
