"""HTTP acceptance scenarios backed by the real MySQL schema."""

import io
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from app import db
from app.cli import upgrade_schema


def create_user(name, role="player"):
    return db.execute(
        "INSERT INTO users(nickname,email,password_hash,role) VALUES(%s,%s,%s,%s)",
        (name, f"{name}@example.test", "unused", role),
    )


def create_game(name, minimum=1, maximum=1, mode="solo"):
    return db.execute(
        "INSERT INTO games(name,team_size_min,team_size_max,format) VALUES(%s,%s,%s,%s)",
        (name, minimum, maximum, mode),
    )


def sign_in(app, user_id=None):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = f"test-token-{user_id}"
    return client


def post(client, path, status=302, **fields):
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(path, data={"csrf_token": token, **fields})
    assert response.status_code == status, response.get_data(as_text=True)[:1000]
    return response


def image(color="#cc8822", name="shared-banner.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, "PNG")
    buffer.seek(0)
    return buffer, name


def draft_fields(game_id, **overrides):
    return {
        "title": "Arena Invitational",
        "description": "Qualifiers and finals for the local esports community.",
        "game_id": game_id,
        "format": "single_elimination",
        "registration_type": "solo",
        "max_participants": 2,
        "start_date": "2030-04-01",
        "end_date": "2030-04-02",
        **overrides,
    }


def registration_for(tournament_id, user_id):
    return db.one(
        "SELECT * FROM registrations WHERE tournament_id=%s AND user_id=%s ORDER BY id DESC LIMIT 1",
        (tournament_id, user_id),
    )


@pytest.mark.fr(
    "FR-006",
    "FR-007",
    "FR-012",
    "FR-013",
    "FR-015",
    "FR-016",
    "FR-017",
    "FR-018",
    "FR-020",
    "FR-021",
    "FR-022",
)
def test_solo_tournament_from_draft_to_public_history(app, client):
    with app.app_context():
        owner = sign_in(app, create_user("SoloOwner", "organizer"))
        entries = {name: create_user(name) for name in ("Alphaplayer", "Betaplayer", "Gammaplayer")}
        players = {name: sign_in(app, user_id) for name, user_id in entries.items()}
        game_id = create_game("Solo Cup Game")

        response = post(owner, "/tournaments/new", **draft_fields(game_id))
        detail = response.headers["Location"]
        tid = int(detail.rsplit("/", 1)[1])

        def tournament_status():
            return db.one("SELECT status FROM tournaments WHERE id=%s", (tid,))["status"]

        assert tournament_status() == "draft"
        assert client.get(detail).status_code == 404
        post(owner, f"{detail}/publish")
        assert tournament_status() == "draft"
        post(owner, f"{detail}/edit", **draft_fields(game_id), banner=image())
        post(owner, f"{detail}/publish")
        assert tournament_status() == "published"
        assert client.get(detail).status_code == 200
        post(owner, f"{detail}/unpublish")
        assert tournament_status() == "draft"
        post(owner, f"{detail}/publish")

        for actor in players.values():
            post(actor, f"{detail}/apply")
        first, second, third = [registration_for(tid, uid)["id"] for uid in entries.values()]
        post(players["Alphaplayer"], f"{detail}/apply")
        assert db.one("SELECT COUNT(*) AS n FROM registrations WHERE tournament_id=%s", (tid,))["n"] == 3
        post(players["Gammaplayer"], f"/registrations/{first}/withdraw", status=404)
        post(owner, f"{detail}/unpublish")
        assert tournament_status() == "published"
        post(owner, f"{detail}/generate-bracket")
        assert tournament_status() == "published"

        for registration_id in (first, second, third):
            post(owner, f"/registrations/{registration_id}/approve")
        post(players["Alphaplayer"], f"/registrations/{first}/withdraw")
        assert [registration_for(tid, uid)["status"] for uid in entries.values()] == [
            "approved",
            "approved",
            "pending",
        ]
        post(owner, f"/registrations/{third}/reject", rejection_reason="no")
        assert registration_for(tid, entries["Gammaplayer"])["status"] == "pending"
        post(owner, f"/registrations/{third}/reject", rejection_reason="Bracket capacity reached")
        assert (
            registration_for(tid, entries["Gammaplayer"])["rejection_reason"]
            == "Bracket capacity reached"
        )

        post(owner, f"{detail}/generate-bracket")
        assert tournament_status() == "in_progress"
        post(owner, f"{detail}/generate-bracket")
        post(owner, f"{detail}/finish")
        post(players["Gammaplayer"], f"{detail}/apply")
        assert tournament_status() == "in_progress"
        assert registration_for(tid, entries["Gammaplayer"])["status"] == "rejected"

        matches = db.all("SELECT * FROM matches WHERE tournament_id=%s", (tid,))
        assert len(matches) == 1
        match = matches[0]
        assert {match["registration1_id"], match["registration2_id"]} == {first, second}
        cabinet = players["Alphaplayer"].get("/my/matches").data
        assert b"Betaplayer" in cabinet
        assert b"01.04.2030" in cabinet
        assert b"Arena Invitational" in players["Alphaplayer"].get("/my/tournaments").data
        assert b"Arena Invitational" not in players["Gammaplayer"].get("/my/matches").data

        result = f"/matches/{match['id']}/result"
        outsider = sign_in(app, create_user("OutsideSoloOwner", "organizer"))
        post(outsider, result, status=404, score1="1", score2="0")
        post(owner, result, score1="-1", score2="0")
        post(owner, result, score1="1", score2="1")
        assert tournament_status() == "in_progress"
        post(owner, result, score1="2", score2="1")
        assert tournament_status() == "completed"
        post(owner, result, score1="0", score2="5")
        row = db.one("SELECT * FROM matches WHERE id=%s", (match["id"],))
        assert (row["score1"], row["score2"], row["winner_registration_id"]) == (
            2,
            1,
            match["registration1_id"],
        )
        assert b"Arena Invitational" not in client.get("/").data
        assert b"Arena Invitational" in client.get("/history").data
        finished = client.get(detail)
        assert finished.status_code == 200
        assert all(name.encode() in finished.data for name in ("SoloOwner", "Alphaplayer", "Betaplayer"))

        # User administration must honor dependent-row cascades, while the public
        # archive must keep the original names and results of completed matches.
        administrator = sign_in(app, create_user("HistoryAdmin", "admin"))
        removed_player_id = entries["Alphaplayer"]
        post(administrator, f"/admin/users/{removed_player_id}/delete")
        assert db.one("SELECT id FROM users WHERE id=%s", (removed_player_id,)) is None
        assert registration_for(tid, removed_player_id) is None
        archived = client.get(detail)
        assert archived.status_code == 200
        for label in (b"bracket-heading", b"participants-heading"):
            section = rb'aria-labelledby="' + label + rb'">(.*?)</section>'
            original = re.search(section, finished.data, re.DOTALL)
            after_deletion = re.search(section, archived.data, re.DOTALL)
            assert original and after_deletion
            assert after_deletion.group(1) == original.group(1)


@pytest.mark.fr("FR-011", "FR-012", "FR-013", "FR-015", "FR-019", "FR-022")
def test_team_registration_checks_roster_and_plays_round_robin(app):
    with app.app_context():
        owner = sign_in(app, create_user("TeamOwner", "organizer"))
        game = create_game("Doubles", 2, 2, "team")
        other_game = create_game("Different Doubles", 1, 2, "team")
        captains = {name: create_user(name) for name in ("NorthCaptain", "SouthCaptain")}
        partners = {name: create_user(name) for name in ("NorthPartner", "SouthPartner")}
        extra = create_user("ExcessPlayer")
        detail = post(
            owner,
            "/tournaments/new",
            **draft_fields(
                game,
                registration_type="team",
                format="round_robin",
                banner=image(),
            ),
        ).headers["Location"]
        tid = int(detail.rsplit("/", 1)[1])
        post(owner, f"{detail}/publish")

        extra_player = sign_in(app, extra)
        post(extra_player, "/teams/new", name="Other Discipline Team", game_id=other_game)
        post(extra_player, f"{detail}/apply")
        assert db.one("SELECT COUNT(*) AS n FROM registrations WHERE tournament_id=%s", (tid,))["n"] == 0

        team_ids = []
        for side in ("North", "South"):
            captain_id = captains[f"{side}Captain"]
            captain = sign_in(app, captain_id)
            post(captain, "/teams/new", name=f"{side} Team", game_id=game, logo=image())
            team = db.one("SELECT id,logo FROM teams WHERE captain_id=%s", (captain_id,))
            assert team["logo"]
            team_id = team["id"]
            team_ids.append(team_id)
            post(captain, f"{detail}/apply")
            assert db.one("SELECT id FROM registrations WHERE team_id=%s", (team_id,)) is None
            members = f"/teams/{team_id}/members"
            post(captain, members, nickname=f"{side}Partner")
            spare_id = create_user(f"{side}Spare")
            post(captain, members, nickname=f"{side}Spare")
            assert db.one("SELECT id FROM team_members WHERE user_id=%s", (spare_id,)) is None
            post(captain, f"{members}/{captain_id}/remove")
            assert (
                db.one("SELECT role FROM team_members WHERE user_id=%s", (captain_id,))["role"]
                == "captain"
            )
            partner = sign_in(app, partners[f"{side}Partner"])
            post(partner, f"{detail}/apply", status=403)
            post(partner, "/teams/new", name=f"{side} Duplicate", game_id=game)
            assert db.one("SELECT id FROM teams WHERE name=%s", (f"{side} Duplicate",)) is None
            post(captain, f"{detail}/apply")
            row = db.one("SELECT * FROM registrations WHERE team_id=%s", (team_id,))
            assert row["user_id"] is None and row["status"] == "pending"
            post(partner, f"/registrations/{row['id']}/withdraw", status=404)
            post(captain, f"/registrations/{row['id']}/withdraw")
            assert (
                db.one("SELECT status FROM registrations WHERE id=%s", (row["id"],))["status"]
                == "withdrawn"
            )
            post(captain, f"{detail}/apply")
            active = db.one(
                "SELECT id FROM registrations WHERE team_id=%s AND status='pending'", (team_id,)
            )
            assert active["id"] != row["id"]
            post(owner, f"/registrations/{active['id']}/approve")
            channel = db.one(
                "SELECT id FROM chat_threads WHERE tournament_id=%s AND participant_user_id=%s",
                (tid, captain_id),
            )
            post(captain, f"/chats/{channel['id']}", text=f"{side} Team is ready")
            assert (
                db.one(
                    "SELECT sender_id FROM chat_messages WHERE thread_id=%s",
                    (channel["id"],),
                )["sender_id"]
                == captain_id
            )

        post(owner, f"{detail}/generate-bracket")
        match = db.one("SELECT id,next_match_id FROM matches WHERE tournament_id=%s", (tid,))
        assert match["next_match_id"] is None
        north_partner = sign_in(app, partners["NorthPartner"])
        assert b"South Team" in north_partner.get("/my/matches").data
        post(owner, f"/matches/{match['id']}/result", score1="0", score2="0")
        assert db.one("SELECT status FROM tournaments WHERE id=%s", (tid,))["status"] == "completed"
        teams = db.all("SELECT DISTINCT team_id FROM registrations WHERE tournament_id=%s", (tid,))
        assert {row["team_id"] for row in teams} == set(team_ids)
        assert b"Arena Invitational" in north_partner.get("/history").data

        db.execute("UPDATE games SET format='solo',team_size_min=1,team_size_max=1 WHERE id=%s", (game,))
        solo = post(
            owner, "/tournaments/new", **draft_fields(game, title="Solo Invitational"), banner=image(),
        ).headers["Location"]
        solo_id = int(solo.rsplit("/", 1)[1])
        post(owner, f"{solo}/publish")
        captain_id = captains["NorthCaptain"]
        north_captain = sign_in(app, captain_id)
        assert "Заявки принимаются от игроков без команды" in north_captain.get(solo).get_data(as_text=True)
        post(north_captain, f"{solo}/apply")
        assert registration_for(solo_id, captain_id) is None


def card_titles(response):
    assert response.status_code == 200
    return re.findall(
        r'<a class="tournament-link" href="[^"]+">([^<]+)</a>', response.get_data(as_text=True)
    )


@pytest.mark.fr("FR-001", "FR-002", "FR-003", "FR-004", "FR-005", "FR-006", "FR-007")
def test_catalog_combines_filters_sorting_and_pagination(app, client):
    assert "Пока нет активных турниров" in client.get("/").get_data(as_text=True)
    with app.app_context():
        owner_id = create_user("CatalogOwner", "organizer")
        other_owner_id = create_user("OtherOwner", "organizer")
        game_id = create_game("Arena Game")
        other_game = create_game("Other Game")

        def add(title, status="published", game=game_id):
            return db.execute(
                """INSERT INTO tournaments(title,description,game_id,organizer_id,format,registration_type,
                   status,max_participants,start_date,end_date,created_at,updated_at,completed_at)
                   VALUES (%s,%s,%s,%s,'single_elimination','solo',%s,8,
                           '2030-04-01','2030-04-02','2030-03-01 00:00:00','2030-04-02 00:00:00',%s)""",
                (
                    title, "Qualifier championship description", game, owner_id, status,
                    "2030-04-02 00:00:00" if status == "completed" else None,
                ),
            )

        expected = [f"Contest {number:02d}" for number in range(13)]
        for title in reversed(expected):
            add(title, "in_progress" if title == expected[-1] else "published")
        hidden = [add("Hidden Contest", status) for status in ("draft", "cancelled")]
        old_archive = add("Archived Contest", "completed")
        add("Other discipline", game=other_game)

        query = {"game": game_id, "q": "championship", "sort": "title_asc"}
        for selector, results in [
            ("abc", expected[:6]),
            ("-5", expected[:6]),
            ("2", expected[6:12]),
            ("999", expected[12:]),
        ]:
            assert card_titles(client.get("/", query_string={**query, "page": selector})) == results

        html = client.get("/", query_string=query).get_data(as_text=True)
        assert f'<option value="{game_id}" selected>' in html
        hrefs = [
            parse_qs(urlparse(unescape(href)).query) for href in re.findall(r'href="([^"]+)"', html)
        ]
        assert {
            "page": ["2"],
            "q": ["championship"],
            "game": [str(game_id)],
            "sort": ["title_asc"],
        } in hrefs

        for sort, results in [
            ("title_desc", expected[-1:-7:-1]),
            ("new_desc", expected[:6]),
            ("new_asc", expected[-1:-7:-1]),
            ("unknown", expected[:6]),
        ]:
            assert card_titles(client.get("/", query_string={**query, "sort": sort})) == results

        assert card_titles(client.get("/?q=Contest+12")) == ["Contest 12"]
        assert card_titles(client.get("/?q=Other+discipline&game=0")) == ["Other discipline"]
        assert "Ничего не найдено" in client.get("/?q=unlisted").get_data(as_text=True)
        assert card_titles(client.get("/history")) == ["Archived Contest"]
        assert card_titles(client.get("/history", query_string={"game": other_game})) == []
        newest_archive = add("Recently Finished", "completed")
        db.execute(
            "UPDATE tournaments SET end_date='2030-04-01',start_date='2030-03-30',"
            "completed_at='2030-04-03 00:00:00' WHERE id=%s",
            (newest_archive,),
        )
        db.execute("UPDATE tournaments SET updated_at='2030-04-04 00:00:00' WHERE id=%s", (old_archive,))
        assert card_titles(client.get("/history")) == ["Recently Finished", "Archived Contest"]
        for tournament_id in hidden:
            detail = f"/tournaments/{tournament_id}"
            assert client.get(detail).status_code == 404
            assert sign_in(app, other_owner_id).get(detail).status_code == 404
            assert sign_in(app, owner_id).get(detail).status_code == 200


@pytest.mark.fr("FR-014", "FR-023", "NFR-004")
def test_chat_restricts_channels_and_sorts_inbox_by_recent_messages(app):
    with app.app_context():
        owner = sign_in(app, create_user("ChatOwner", "organizer"))
        other = sign_in(app, create_user("OutsideOwner", "organizer"))
        first_id = create_user("FirstChatter")
        second_id = create_user("SecondChatter")
        first, second = sign_in(app, first_id), sign_in(app, second_id)
        game = create_game("Chat Game")
        detail = post(owner, "/tournaments/new", **draft_fields(game, banner=image())).headers[
            "Location"
        ]
        post(owner, f"{detail}/publish")
        for actor in (first, second):
            post(actor, f"{detail}/apply")
        channels = {
            row["participant_user_id"]: row["id"]
            for row in db.all(
                "SELECT id,participant_user_id FROM chat_threads",
            )
        }
        first_channel = f"/chats/{channels[first_id]}"
        second_channel = f"/chats/{channels[second_id]}"
        registration_id = db.one("SELECT id FROM registrations WHERE user_id=%s", (first_id,))["id"]
        post(first, f"/registrations/{registration_id}/withdraw")
        post(first, first_channel, text="First application withdrawn")
        post(second, second_channel, text="Second application submitted")
        assert first.get(second_channel).status_code == 403
        assert second.get(f"{first_channel}/messages").status_code == 403
        assert other.get(first_channel).status_code == 403
        post(second, first_channel, status=403, text="Access is restricted")

        inbox = f"{detail}/chats"
        original = owner.get(inbox).get_data(as_text=True)
        assert original.index("Second application submitted") < original.index(
            "First application withdrawn"
        )
        post(owner, first_channel, text="First application received")
        updated = owner.get(inbox).get_data(as_text=True)
        assert updated.index("First application received") < updated.index(
            "Second application submitted"
        )

        response = first.get(first_channel)
        assert response.status_code == 200
        assert b"First application withdrawn" in response.data
        assert b"First application received" in response.data
        payload = first.get(f"{first_channel}/messages").get_json()["messages"]
        assert payload[0]["sender_id"] == first_id
        assert [row["text"] for row in payload] == [
            "First application withdrawn",
            "First application received",
        ]
        fresh = first.get(f"{first_channel}/messages?after={payload[0]['id']}").get_json()["messages"]
        assert fresh == payload[1:]


@pytest.mark.fr("FR-024", "FR-025")
def test_admin_crud_enforces_dependencies_and_self_protection(app):
    with app.app_context():
        admin_id = create_user("FirstAdmin", "admin")
        admin = sign_in(app, admin_id)
        profile = f"/admin/users/{admin_id}"
        post(admin, profile, role="player")
        post(admin, f"{profile}/delete")
        assert db.one("SELECT role FROM users WHERE id=%s", (admin_id,))["role"] == "admin"

        people = [("AdminOrganizer", "organizer"), ("AdminPlayer", "player"), ("BackupAdmin", "admin")]
        for nickname, role in people:
            post(
                admin,
                "/admin/users",
                nickname=nickname,
                email=f"{nickname}@example.test",
                password="sample password",
                role=role,
            )
        organizer_id = db.one("SELECT id FROM users WHERE nickname='AdminOrganizer'")["id"]
        player_id = db.one("SELECT id FROM users WHERE nickname='AdminPlayer'")["id"]
        post(admin, f"/admin/users/{player_id}", email="edited@example.test", role="organizer")
        assert db.one("SELECT email,role FROM users WHERE id=%s", (player_id,)) == {
            "email": "edited@example.test",
            "role": "organizer",
        }

        fields = {"name": "Managed Game", "team_size_min": 1, "team_size_max": 1, "format": "solo"}
        post(admin, "/admin/games", **{**fields, "team_size_max": 2})
        post(admin, "/admin/games", **{**fields, "team_size_min": 3, "format": "team"})
        assert db.one("SELECT id FROM games") is None
        post(admin, "/admin/games", **fields)
        post(admin, "/admin/games", **fields)
        assert db.one("SELECT COUNT(*) AS n FROM games")["n"] == 1
        game_id = db.one("SELECT id FROM games")["id"]
        game_path = f"/admin/games/{game_id}"
        post(admin, game_path, **{**fields, "name": "Renamed Game"})
        assert db.one("SELECT name FROM games WHERE id=%s", (game_id,))["name"] == "Renamed Game"
        owner = sign_in(app, organizer_id)
        post(owner, "/tournaments/new", **draft_fields(game_id))
        post(admin, f"/admin/users/{organizer_id}/delete")
        post(admin, f"{game_path}/delete")
        assert db.one("SELECT id FROM games WHERE id=%s", (game_id,)) is not None
        assert db.one("SELECT id FROM users WHERE id=%s", (organizer_id,)) is not None
        post(admin, f"/admin/users/{player_id}/delete")
        assert db.one("SELECT id FROM users WHERE id=%s", (player_id,)) is None
        spare = create_game("Unreferenced Game")
        post(admin, f"/admin/games/{spare}/delete")
        assert db.one("SELECT id FROM games WHERE id=%s", (spare,)) is None


@pytest.mark.fr("FR-012", "FR-015", "FR-016", "NFR-003", "NFR-004")
def test_organizer_can_delete_drafts_and_cancel_active_tournaments(app, client):
    with app.app_context():
        owner = sign_in(app, create_user("LifecycleOwner", "organizer"))
        outsider = sign_in(app, create_user("LifecycleOutsider", "organizer"))
        player_ids = [create_user(f"LifecyclePlayer{i}") for i in range(3)]
        first, second, extra = [sign_in(app, player_id) for player_id in player_ids]
        fields = draft_fields(create_game("Lifecycle Solo"))

        detail = post(owner, "/tournaments/new", **fields, banner=image()).headers["Location"]
        draft_id = int(detail.rsplit("/", 1)[1])
        uploads = Path(app.config["UPLOAD_FOLDER"])
        old_file = uploads / db.one("SELECT banner FROM tournaments WHERE id=%s", (draft_id,))["banner"]
        assert old_file.is_file()
        post(owner, f"{detail}/edit", **fields, banner=image(color="#66ab44"))
        new_file = uploads / db.one("SELECT banner FROM tournaments WHERE id=%s", (draft_id,))["banner"]
        assert new_file.is_file()
        assert not old_file.exists()
        post(outsider, f"{detail}/delete", status=404)
        assert owner.post(f"{detail}/delete").status_code == 403
        post(owner, f"{detail}/delete")
        assert owner.get(detail).status_code == 404
        assert not new_file.exists()

        detail = post(owner, "/tournaments/new", **fields, banner=image()).headers["Location"]
        tournament_id = int(detail.rsplit("/", 1)[1])
        post(owner, f"{detail}/publish")
        post(owner, f"{detail}/delete")
        assert client.get(detail).status_code == 200
        for actor, player_id in zip((first, second), player_ids[:2], strict=True):
            post(actor, f"{detail}/apply")
            registration_id = registration_for(tournament_id, player_id)["id"]
            post(owner, f"/registrations/{registration_id}/approve")
        assert "Все места заняты." in extra.get(detail).get_data(as_text=True)
        post(extra, f"{detail}/apply")
        assert registration_for(tournament_id, player_ids[2]) is None

        post(owner, f"{detail}/generate-bracket")
        match_id = db.one("SELECT id FROM matches WHERE tournament_id=%s", (tournament_id,))["id"]
        post(outsider, f"{detail}/cancel", status=404)
        post(owner, f"{detail}/cancel")
        post(owner, f"/matches/{match_id}/result", score1="2", score2="1")
        assert db.one("SELECT status FROM matches WHERE id=%s", (match_id,))["status"] == "cancelled"
        assert client.get(detail).status_code == first.get(detail).status_code == 404
        assert owner.get(detail).status_code == 200
        assert b"Arena Invitational" in first.get("/my/tournaments").data
        assert b"Arena Invitational" in first.get("/my/matches").data
        assert "Отменён" in first.get("/my/tournaments").get_data(as_text=True)
        assert b"Arena Invitational" not in extra.get("/my/tournaments").data
        assert b"Arena Invitational" not in client.get("/history").data


@pytest.mark.fr("FR-007", "FR-015")
def test_upgrade_preserves_results_and_fills_legacy_dates(app, client):
    with app.app_context():
        owner = sign_in(app, create_user("LegacyOwner", "organizer"))
        player_ids = [create_user(f"LegacyPlayer{i}") for i in range(2)]
        fields = draft_fields(create_game("Legacy Solo"))
        detail = post(owner, "/tournaments/new", **fields, banner=image()).headers["Location"]
        tid = int(detail.rsplit("/", 1)[1])
        post(owner, f"{detail}/publish")
        for player_id in player_ids:
            post(sign_in(app, player_id), f"{detail}/apply")
            registration_id = registration_for(tid, player_id)["id"]
            post(owner, f"/registrations/{registration_id}/approve")
        post(owner, f"{detail}/generate-bracket")
        match = db.one("SELECT id FROM matches WHERE tournament_id=%s", (tid,))
        mid = match["id"]
        db.execute("UPDATE matches SET scheduled_at=NULL WHERE id=%s", (mid,))
        post(owner, f"/matches/{mid}/result", score1="2", score2="1")
        db.execute("UPDATE tournaments SET updated_at='2030-04-02 15:00:00' WHERE id=%s", (tid,))
        old = db.one("SELECT results_snapshot,updated_at FROM tournaments WHERE id=%s", (tid,))
        db.execute(
            """ALTER TABLE tournaments DROP INDEX idx_tournaments_history, DROP COLUMN completed_at,
               ADD INDEX idx_tournaments_history (status, updated_at, id)"""
        )
        try:
            runner = app.test_cli_runner()
            for _ in range(2):
                result = runner.invoke(args=["upgrade-db"])
                assert result.exit_code == 0, result.exception
            saved = db.one(
                "SELECT results_snapshot,updated_at,completed_at FROM tournaments WHERE id=%s", (tid,)
            )
            assert saved["completed_at"] == saved["updated_at"] == old["updated_at"]
            assert saved["results_snapshot"] == old["results_snapshot"]
            dates = db.one("SELECT DATE(scheduled_at) AS date FROM matches WHERE id=%s", (mid,))
            assert str(dates["date"]) == fields["start_date"]
            assert b"01.04.2030" in client.get(detail).data
            assert b"01.04.2030" in sign_in(app, player_ids[0]).get("/my/matches").data
        finally:
            # The database fixture is reused by later scenarios.
            upgrade_schema()
