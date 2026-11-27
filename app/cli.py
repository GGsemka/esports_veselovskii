"""Local database setup. Demo and benchmark datasets are explicit commands."""

from datetime import date, timedelta
from pathlib import Path

import click
from flask import g
from werkzeug.security import generate_password_hash

from . import db
from .brackets import round_dates
from .competition import generate_bracket, record_result

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"
GAMES = [
    ("Counter-Strike 2", 5, 5, "team", "cs2"),
    ("Dota 2", 5, 5, "team", "dota"),
    ("VALORANT", 5, 5, "team", "valorant"),
    ("League of Legends", 5, 5, "team", "lol"),
    ("EA SPORTS FC", 1, 1, "solo", "fc"),
    ("Standoff 2", 5, 5, "team", "standoff"),
]
TITLES = [
    "Линия огня",
    "Кубок Аегиса",
    "Спектр • Open Cup",
    "Лига Нексуса",
    "Кубок чемпионов",
    "Точка входа",
    "Весенняя лига",
    "Осенний финал",
]
DEMO_NAMES = ["arena_org", "arena_admin", "arena_player", "arena_rival"]


def init_schema():
    # MySQL client DELIMITER is needed for the two multi-statement triggers.
    delimiter = ";"
    statement = []
    with db.connect().cursor() as cur:
        for line in SCHEMA.read_text().splitlines():
            if not line.strip() or line.startswith("--"):
                continue
            if line.startswith("DELIMITER "):
                delimiter = line.split()[1]
                continue
            statement.append(line)
            if line.endswith(delimiter):
                cur.execute("\n".join(statement)[: -len(delimiter)])
                statement.clear()
    if statement:
        raise RuntimeError("Незавершённая инструкция SQL.")


def upgrade_schema():
    if not db.one("SHOW COLUMNS FROM tournaments WHERE Field=%s", ("completed_at",)):
        db.execute(
            """ALTER TABLE tournaments ADD COLUMN completed_at DATETIME NULL,
               DROP INDEX idx_tournaments_history,
               ADD INDEX idx_tournaments_history (status, completed_at, id)"""
        )

    with db.transaction():
        db.execute(
            """UPDATE tournaments SET completed_at=updated_at,updated_at=updated_at
               WHERE status='completed' AND completed_at IS NULL"""
        )
        tournaments = db.all(
            """SELECT t.id,t.start_date,t.end_date,MAX(m.round_number) AS round_count
               FROM tournaments t JOIN matches m ON m.tournament_id=t.id
               GROUP BY t.id HAVING COUNT(m.scheduled_at)<COUNT(*)"""
        )
        for tournament in tournaments:
            dates = round_dates(tournament["start_date"], tournament["end_date"], tournament["round_count"])
            for number, planned_date in enumerate(dates, 1):
                db.execute(
                    """UPDATE matches SET scheduled_at=%s
                       WHERE tournament_id=%s AND round_number=%s AND scheduled_at IS NULL""",
                    (planned_date, tournament["id"], number),
                )


def empty_database():
    if db.one("SELECT id FROM users LIMIT 1") or db.one("SELECT id FROM games LIMIT 1"):
        raise click.ClickException("Команда предназначена для пустой базы. Данные сохранены.")


def seed(password, benchmark=False):
    empty_database()
    if not benchmark and len(password) < 8:
        raise click.ClickException("Демо-пароль должен содержать минимум восемь символов.")
    hashed = generate_password_hash(password)
    today = date.today()
    with db.transaction():
        ids = [
            db.execute(
                "INSERT INTO users(nickname,email,password_hash,role) VALUES(%s,%s,%s,%s)",
                (name, name + "@example.test", hashed, role),
            )
            for name, role in zip(DEMO_NAMES, ["organizer", "admin", "player", "player"], strict=True)
        ]
        games = [
            db.execute(
                "INSERT INTO games(name,team_size_min,team_size_max,format) VALUES(%s,%s,%s,%s)",
                game[:4],
            )
            for game in GAMES
        ]
        tournaments = []
        for i in range(1000 if benchmark else 8):
            idx = i % len(games) if benchmark or i < 6 else 4
            name, _, _, kind, art = GAMES[idx]
            title = f"Тестовая лига {i:04d}" if benchmark else TITLES[i]
            start = today + timedelta(days=i % 14 + 1)
            if not benchmark and i >= 6:
                start = today - timedelta(days=8 if i == 7 else 1)
            tournaments.append(
                db.execute(
                    """INSERT INTO tournaments
                (title,description,game_id,organizer_id,format,registration_type,status,max_participants,
                 start_date,end_date,banner,created_at) VALUES(%s,%s,%s,%s,%s,%s,'published',%s,%s,%s,%s,%s)""",
                    (
                        title,
                        f"Открытый турнир по {name}. Участники играют за место в итоговой таблице.",
                        games[idx],
                        ids[0],
                        "round_robin" if i % 2 else "single_elimination",
                        kind,
                        16,
                        start,
                        start + timedelta(days=3),
                        f"art/{art}.svg",
                        today - timedelta(days=i % 100),
                    ),
                )
            )
        if not benchmark:
            for tid in tournaments[-2:]:
                for user in ids[2:]:
                    db.execute(
                        "INSERT INTO registrations(tournament_id,user_id,status) VALUES(%s,%s,'approved')",
                        (tid, user),
                    )
                    db.execute(
                        "INSERT INTO chat_threads(tournament_id,participant_user_id) VALUES(%s,%s)",
                        (tid, user),
                    )
    if not benchmark:
        g.user = {"id": ids[0], "role": "organizer"}
        for tid in tournaments[-2:]:
            generate_bracket(tid)
        match = db.one("SELECT id FROM matches WHERE tournament_id=%s", (tournaments[-1],))
        record_result(match["id"], {"score1": "2", "score2": "1"})


def seed_featured_tournaments():
    """Add the showcase tournaments to an existing demo database."""
    titles = (
        ("Deadlock", 6, "Кубок перекрёстков"),
        ("Overwatch", 5, "Фронт арены"),
    )
    with db.transaction():
        owner = db.one(
            "SELECT id FROM users WHERE nickname=%s AND role='organizer' FOR UPDATE",
            ("arena_org",),
        )
        if not owner:
            raise click.ClickException("Сначала наполните локальную демобазу командой seed-demo.")
        for game, size, title in titles:
            db.execute(
                """INSERT INTO games(name,team_size_min,team_size_max,format)
                   VALUES(%s,%s,%s,'team') ON DUPLICATE KEY UPDATE id=id""",
                (game, size, size),
            )
            game_id = db.one("SELECT id FROM games WHERE name=%s", (game,))["id"]
            if db.one(
                "SELECT id FROM tournaments WHERE organizer_id=%s AND game_id=%s AND title=%s",
                (owner["id"], game_id, title),
            ):
                continue
            db.execute(
                """INSERT INTO tournaments
                   (title,description,game_id,organizer_id,format,registration_type,status,
                    max_participants,start_date,end_date,banner)
                   VALUES(%s,%s,%s,%s,'single_elimination','team','published',16,%s,%s,%s)""",
                (
                    title,
                    f"Открытый командный турнир по {game}. Соберите состав и подайте заявку.",
                    game_id,
                    owner["id"],
                    date.today() + timedelta(days=14),
                    date.today() + timedelta(days=16),
                    f"art/feature-{game.lower()}.webp",
                ),
            )


def register(app):
    @app.cli.command("init-db")
    def init():
        if db.one("SHOW TABLES"):
            raise click.ClickException("init-db предназначен для пустой БД.")
        init_schema()
        click.echo("Схема создана.")

    @app.cli.command("upgrade-db")
    def upgrade():
        """Upgrade an existing Arena database."""
        if not db.one("SHOW TABLES LIKE %s", ("tournaments",)):
            raise click.ClickException("Сначала создайте схему командой init-db.")
        upgrade_schema()
        click.echo("Схема обновлена, существующие данные сохранены.")

    @app.cli.command("seed-demo")
    @click.option(
        "--password",
        prompt="Пароль демо-аккаунтов",
        hide_input=True,
        confirmation_prompt=True,
        envvar="DEMO_PASSWORD",
    )
    def demo(password):
        seed(password)
        seed_featured_tournaments()
        click.echo("Демо-аккаунты: " + ", ".join(DEMO_NAMES))

    @app.cli.command("seed-featured-tournaments")
    def featured_tournaments():
        seed_featured_tournaments()
        click.echo("Демо-турниры Deadlock и Overwatch доступны в каталоге.")

    @app.cli.command("seed-benchmark")
    def benchmark():
        import secrets

        seed(secrets.token_hex(32), benchmark=True)
        click.echo("Создано 1000 опубликованных турниров.")
