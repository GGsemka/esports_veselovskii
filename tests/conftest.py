"""Use a fresh, randomly named MySQL database; never truncate a configured app database."""

import os
from uuid import uuid4

import pymysql
import pytest
from dotenv import load_dotenv

from app import create_app, db
from app.cli import init_schema


@pytest.fixture(scope="session")
def database():
    load_dotenv()
    name = "arena_test_" + uuid4().hex
    options = {
        "host": os.getenv("TEST_DB_HOST", os.getenv("DB_HOST", "127.0.0.1")),
        "port": int(os.getenv("TEST_DB_PORT", os.getenv("DB_PORT", "3306"))),
        "unix_socket": os.getenv("TEST_DB_SOCKET", os.getenv("DB_SOCKET")) or None,
        "user": os.getenv("TEST_DB_USER", "root"),
        "password": os.getenv("TEST_DB_PASSWORD", ""),
    }
    with pymysql.connect(**options, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE DATABASE `" + name + "` CHARACTER SET utf8mb4")
        try:
            yield {
                "DB_NAME": name,
                "DB_HOST": options["host"],
                "DB_USER": options["user"],
                "DB_PASSWORD": options["password"],
                "DB_PORT": options["port"],
                "DB_SOCKET": options["unix_socket"],
            }
        finally:
            with conn.cursor() as cur:
                cur.execute("DROP DATABASE `" + name + "`")


@pytest.fixture
def app(database, tmp_path):
    app = create_app("test", {**database, "UPLOAD_FOLDER": str(tmp_path), "SECRET_KEY": "test session"})
    with app.app_context():
        if not db.one("SHOW TABLES"):
            init_schema()
        else:
            # Only the generated test database is connected here. Delete children first.
            for table in (
                "chat_messages",
                "chat_threads",
                "matches",
                "registrations",
                "team_members",
                "teams",
                "tournaments",
                "users",
                "games",
            ):
                db.execute("DELETE FROM " + table)
    return app


@pytest.fixture
def client(app):
    return app.test_client()
