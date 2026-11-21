import os
from pathlib import Path
from secrets import token_hex


def configuration(name, instance_path):
    if name not in {"dev", "test", "prod"}:
        raise ValueError("APP_ENV должен быть dev, test или prod.")
    secret = os.getenv("SECRET_KEY")
    if name == "prod" and (not secret or len(secret) < 32):
        raise RuntimeError("В prod задайте SECRET_KEY длиной не менее 32 символов.")
    return {
        "SECRET_KEY": secret or token_hex(32),
        "TESTING": name == "test",
        "DEBUG": False,
        "PER_PAGE": 6,
        "MAX_CONTENT_LENGTH": 4 * 1024 * 1024,
        "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "DB_PORT": int(os.getenv("DB_PORT", "3306")),
        "DB_NAME": os.getenv("DB_NAME", "arena"),
        "DB_SOCKET": os.getenv("DB_SOCKET"),
        "DB_USER": os.getenv("DB_USER", "arena"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""),
        "UPLOAD_FOLDER": os.getenv("UPLOAD_FOLDER") or str(Path(instance_path) / "uploads"),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": name == "prod" and os.getenv("HTTPS_ONLY", "1") != "0",
    }
