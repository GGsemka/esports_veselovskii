"""Server-side safeguards exercised through application routes and MySQL constraints."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pymysql import MySQLError
from test_workflow import create_game, create_user, draft_fields, image, post, sign_in
from werkzeug.security import check_password_hash

from app import db


@pytest.mark.fr("NFR-003")
def test_every_registered_post_route_requires_its_own_session_token(app):
    client = sign_in(app)
    other_client = app.test_client()
    with other_client.session_transaction() as session:
        session["csrf_token"] = "different-session-token"
    with other_client.session_transaction() as session:
        foreign_token = session["csrf_token"]
    adapter = app.url_map.bind("localhost")

    post_routes = [rule for rule in app.url_map.iter_rules() if "POST" in rule.methods]
    assert post_routes
    for rule in post_routes:
        path = adapter.build(rule.endpoint, {arg: 1 for arg in rule.arguments}, method="POST")
        assert client.post(path).status_code == 403, path
        assert client.post(path, data={"csrf_token": foreign_token}).status_code == 403, path


@pytest.mark.fr("FR-008", "FR-009", "FR-010", "NFR-001")
def test_account_validation_password_storage_and_profile_credentials(app):
    visitor = sign_in(app)
    registration = {
        "nickname": "New_Player",
        "email": "newplayer@example.test",
        "password": "original password",
        "confirm": "original password",
        "role": "organizer",
    }
    with app.app_context():
        invalid = post(
            visitor,
            "/register",
            status=200,
            **{
                **registration,
                "nickname": "aa",
                "email": "invalid-email",
                "password": "short",
            },
        )
        assert b"invalid-email" in invalid.data
        assert "Пароли не совпадают" in invalid.get_data(as_text=True)
        assert db.one("SELECT id FROM users") is None
        assert post(visitor, "/register", **registration).headers["Location"] == "/login"
        user = db.one("SELECT * FROM users WHERE nickname=%s", (registration["nickname"],))
        assert user["role"] == "player"
        assert user["password_hash"] != registration["password"]
        assert check_password_hash(user["password_hash"], registration["password"])
        post(visitor, "/register", status=200, **registration)
        assert db.one("SELECT COUNT(*) AS n FROM users")["n"] == 1

        for login in ("unknown@example.test", registration["email"]):
            wrong = post(visitor, "/login", status=200, login=login, password="bad password")
            assert "Неверный логин или пароль" in wrong.get_data(as_text=True)
        post(visitor, "/login", login=registration["email"], password=registration["password"])
        with visitor.session_transaction() as session:
            assert session["user_id"] == user["id"]

        def refresh_csrf():
            with visitor.session_transaction() as session:
                session["csrf_token"] = "authenticated-test-token"

        refresh_csrf()
        post(visitor, "/profile", nickname=registration["nickname"], email=registration["email"])
        create_user("AlreadyTaken")
        post(visitor, "/profile", status=200, nickname="AlreadyTaken", email=registration["email"])
        post(
            visitor,
            "/profile",
            nickname="Updated_Player",
            email="updated@example.test",
            password="replacement password",
            confirm="replacement password",
        )
        post(visitor, "/logout")
        with visitor.session_transaction() as session:
            assert "user_id" not in session
        refresh_csrf()
        post(visitor, "/login", status=200, login="Updated_Player", password=registration["password"])
        post(visitor, "/login", login="Updated_Player", password="replacement password")


@pytest.mark.fr("FR-006", "FR-016", "FR-024", "FR-025", "NFR-004")
def test_player_admin_and_other_organizer_cannot_operate_owner_tournament(app):
    with app.app_context():
        owner = sign_in(app, create_user("RealOwner", "organizer"))
        other = sign_in(app, create_user("OtherOrganiser", "organizer"))
        admin = sign_in(app, create_user("ArenaAdmin", "admin"))
        player = sign_in(app, create_user("Viewer"))
        game = create_game("Secure Tournament")
        detail = post(owner, "/tournaments/new", **draft_fields(game)).headers["Location"]

        for actor in (player, admin):
            assert actor.get(detail).status_code == 404
            assert actor.get("/tournaments/new").status_code == 403
            post(actor, "/tournaments/new", status=403, **draft_fields(game))
            post(actor, f"{detail}/publish", status=403)
            post(actor, f"{detail}/generate-bracket", status=403)
        for suffix in ("edit", "registrations"):
            assert other.get(f"{detail}/{suffix}").status_code == 404
        assert other.get(f"{detail}/chats").status_code == 403
        for suffix in ("publish", "finish", "generate-bracket"):
            post(other, f"{detail}/{suffix}", status=404)
        for actor in (player, owner, other):
            assert actor.get("/admin/users").status_code == 403
            assert actor.get("/admin/games").status_code == 403
        assert admin.get("/admin/users").status_code == 200
        assert admin.get("/admin/games").status_code == 200
        assert db.one("SELECT COUNT(*) AS n FROM tournaments")["n"] == 1


@pytest.mark.fr("FR-012")
def test_database_enforces_exclusive_registration_owner_on_insert_and_update(app):
    with app.app_context():
        user_id = create_user("RosterOwner")
        organizer = sign_in(app, create_user("DbOrganiser", "organizer"))
        game = create_game("Fixture Doubles", 2, 2, "team")
        tid = int(
            post(
                organizer,
                "/tournaments/new",
                **draft_fields(
                    game,
                    registration_type="team",
                ),
            )
            .headers["Location"]
            .rsplit("/", 1)[1]
        )
        team_id = db.execute(
            "INSERT INTO teams(name,captain_id,game_id) VALUES (%s,%s,%s)",
            ("Exclusive Team", user_id, game),
        )
        sql = "INSERT INTO registrations(tournament_id,team_id,user_id) VALUES (%s,%s,%s)"
        valid_id = db.execute(sql, (tid, team_id, None))
        db.execute(sql, (tid, None, user_id))
        for team, user in ((None, None), (team_id, user_id)):
            with pytest.raises(MySQLError) as error:
                db.execute(sql, (tid, team, user))
            assert error.value.args[0] in {1048, 1644, 3819}
            with pytest.raises(MySQLError) as error:
                db.execute(
                    "UPDATE registrations SET team_id=%s,user_id=%s WHERE id=%s", (team, user, valid_id)
                )
            assert error.value.args[0] in {1048, 1644, 3819}
        assert db.one("SELECT COUNT(*) AS n FROM registrations")["n"] == 2


@pytest.mark.fr("FR-016", "NFR-005", "NFR-006")
def test_banner_limits_and_distinct_upload_names(app):
    with app.app_context():
        organizer = sign_in(app, create_user("UploadOwner", "organizer"))
        game = create_game("Upload Game")
        upload_dir = Path(app.config["UPLOAD_FOLDER"])
        for file, name in (
            (b"text", "banner.txt"),
            (b"invalid image bytes", "banner.png"),
            (b"0" * (1024 * 1024 + 1), "large.png"),
        ):
            post(
                organizer,
                "/tournaments/new",
                status=200,
                **draft_fields(
                    game,
                    banner=(BytesIO(file), name),
                ),
            )
        post(organizer, "/tournaments/new", status=200, **draft_fields(game, banner=image(name="png")))
        post(
            organizer,
            "/tournaments/new",
            status=413,
            **draft_fields(
                game,
                banner=(BytesIO(b"0" * (4 * 1024 * 1024 + 1)), "oversized.png"),
            ),
        )
        assert db.one("SELECT COUNT(*) AS n FROM tournaments")["n"] == 0
        assert not list(upload_dir.iterdir())
        colors = ("#cc8822", "#2288cc")
        for color in colors:
            post(organizer, "/tournaments/new", **draft_fields(game, banner=image(color)))
        banners = [row["banner"] for row in db.all("SELECT banner FROM tournaments ORDER BY id")]
        assert len(set(banners)) == 2
        post(organizer, "/tournaments/new", **draft_fields(game, banner=image(name="баннер.png")))
        assert db.one("SELECT COUNT(*) AS n FROM tournaments WHERE banner IS NOT NULL")["n"] == 3
        for name, color in zip(banners, colors, strict=True):
            assert name != "shared-banner.png"
            assert Path(name).name == name and name.endswith(".png")
            response = organizer.get(f"/uploads/{name}")
            assert response.status_code == 200
            assert response.mimetype == "image/png"
            with Image.open(BytesIO(response.data)) as saved:
                assert saved.convert("RGB").getpixel((0, 0)) == Image.new("RGB", (1, 1), color).getpixel(
                    (0, 0),
                )


@pytest.mark.fr("FR-016")
@pytest.mark.parametrize(
    "invalid",
    [
        {"title": "four"},
        {"title": "x" * 121},
        {"description": "x" * 29},
        {"description": "x" * 2001},
        {"max_participants": 1},
        {"max_participants": 65},
        {"end_date": "2030-04-01"},
        {"registration_type": "team"},
    ],
)
def test_tournament_bounds_are_enforced_server_side(app, invalid):
    with app.app_context():
        owner = sign_in(app, create_user("BoundsOwner", "organizer"))
        game = create_game("Bounds Game")
        post(owner, "/tournaments/new", status=200, **draft_fields(game, **invalid))
        assert db.one("SELECT COUNT(*) AS n FROM tournaments")["n"] == 0
