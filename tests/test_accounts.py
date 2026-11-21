"""Account and roster cases beyond the end-to-end tournament workflows."""

import pytest
from test_workflow import create_game, create_user, draft_fields, image, post, sign_in

from app import db


@pytest.mark.fr("NFR-016")
def test_authentication_audit_log(app, caplog):
    visitor = sign_in(app)
    password = "correct-password"
    post(
        visitor,
        "/register",
        nickname="Audit_Player",
        email="audit@example.test",
        password=password,
        confirm=password,
    )
    post(visitor, "/login", status=200, login="Audit_Player", password="wrong-password")
    post(visitor, "/login", login="Audit_Player", password=password)
    with visitor.session_transaction() as session:
        user_id = session["user_id"]
        session["csrf_token"] = "signed-in-test"
    post(visitor, "/logout")

    events = {(row.levelname, row.getMessage()) for row in caplog.records}
    assert ("WARNING", "login rejected") in events
    assert ("INFO", f"login succeeded authenticated_user_id={user_id}") in events
    assert ("INFO", "logout succeeded") in events
    assert password not in caplog.text
    assert "wrong-password" not in caplog.text


@pytest.mark.fr("FR-011")
def test_team_removal_observes_minimum_and_captain_ownership(app):
    with app.app_context():
        captain = create_user("Captain")
        member = create_user("Member")
        reserve = create_user("Reserve")
        team_game = create_game("Team Arena", 2, 3, "team")
    owner = sign_in(app, captain)
    member_client = sign_in(app, member)
    post(owner, "/teams/new", name="Alpha", game_id=team_game)
    with app.app_context():
        team = db.one("SELECT id FROM teams WHERE captain_id=%s", (captain,))
    roster = f"/teams/{team['id']}/members"
    post(owner, roster, nickname="Member")
    post(member_client, roster, status=403, nickname="Reserve")
    post(owner, f"{roster}/{member}/remove")
    with app.app_context():
        assert db.one("SELECT id FROM team_members WHERE user_id=%s", (member,))
    post(owner, roster, nickname="Reserve")
    post(owner, f"{roster}/{reserve}/remove")
    with app.app_context():
        assert {
            row["user_id"]
            for row in db.all("SELECT user_id FROM team_members WHERE team_id=%s", (team["id"],))
        } == {captain, member}


@pytest.mark.fr("FR-024")
def test_admin_protects_active_participants(app):
    with app.app_context():
        admin = create_user("Tournament_Admin", "admin")
        organizer = create_user("Tournament_Owner", "organizer")
        entrant = create_user("Active_Solo")
        bracket_entrant = create_user("Bracket_Solo")
        waitlisted = create_user("Waitlisted_Solo")
        captain = create_user("Active_Captain")
        member = create_user("Active_Member")
        solo_game = create_game("Solo Game")
        team_game = create_game("Team Game", 2, 3, "team")
        team = db.execute(
            "INSERT INTO teams(name,captain_id,game_id) VALUES('Active Team',%s,%s)",
            (captain, team_game),
        )
        for user_id, role in ((captain, "captain"), (member, "player")):
            db.execute(
                "INSERT INTO team_members(team_id,user_id,role) VALUES(%s,%s,%s)", (team, user_id, role)
            )

        def tournament(name, game_id, registration_type, status):
            return db.execute(
                """INSERT INTO tournaments(title,description,game_id,organizer_id,format,registration_type,
                   status,max_participants,start_date,end_date)
                   VALUES(%s,'Test cup',%s,%s,'single_elimination',%s,%s,2,'2030-01-01','2030-01-02')""",
                (name, game_id, organizer, registration_type, status),
            )

        solo = tournament("Solo Cup", solo_game, "solo", "published")
        solo_bracket = tournament("Solo Bracket Cup", solo_game, "solo", "in_progress")
        squads = tournament("Team Cup", team_game, "team", "in_progress")
        db.execute(
            "INSERT INTO registrations(tournament_id,user_id,status) VALUES(%s,%s,'approved')",
            (solo, entrant),
        )
        solo_registration = db.execute(
            "INSERT INTO registrations(tournament_id,user_id,status) VALUES(%s,%s,'approved')",
            (solo_bracket, bracket_entrant),
        )
        db.execute(
            "INSERT INTO registrations(tournament_id,user_id,status) VALUES(%s,%s,'approved')",
            (solo_bracket, waitlisted),
        )
        db.execute(
            """INSERT INTO matches(tournament_id,round_number,match_number,registration1_id)
               VALUES(%s,1,1,%s)""",
            (solo_bracket, solo_registration),
        )
        registration = db.execute(
            "INSERT INTO registrations(tournament_id,team_id,status) VALUES(%s,%s,'approved')",
            (squads, team),
        )
        db.execute(
            """INSERT INTO matches(tournament_id,round_number,match_number,registration1_id)
                      VALUES(%s,1,1,%s)""",
            (squads, registration),
        )
    client = sign_in(app, admin)
    for user_id in (captain, member, organizer, bracket_entrant):
        post(client, f"/admin/users/{user_id}/delete")
        with app.app_context():
            assert db.one("SELECT id FROM users WHERE id=%s", (user_id,))
    for user_id in (entrant, waitlisted):
        post(client, f"/admin/users/{user_id}/delete")
        with app.app_context():
            assert not db.one("SELECT id FROM users WHERE id=%s", (user_id,))
    with app.app_context():
        db.execute("UPDATE tournaments SET status='completed' WHERE id=%s", (squads,))
    for user_id in (member, captain):
        post(client, f"/admin/users/{user_id}/delete")
        with app.app_context():
            assert not db.one("SELECT id FROM users WHERE id=%s", (user_id,))
    with app.app_context():
        assert not db.one("SELECT id FROM teams WHERE id=%s", (team,))
        assert db.one("SELECT id FROM tournaments WHERE id=%s", (squads,))


@pytest.mark.fr("FR-015", "FR-024")
@pytest.mark.parametrize("mode", ["solo", "team"])
def test_completed_match_keeps_original_opponent_and_score(app, mode):
    with app.app_context():
        organizer = sign_in(app, create_user("History_Owner", "organizer"))
        administrator = sign_in(app, create_user("History_Admin", "admin"))
        game = create_game("History Arena", 1, 2 if mode == "team" else 1, mode)
        rival_id = create_user("Original_Rival")
        rival = sign_in(app, rival_id)
        player = sign_in(app, create_user("Remaining_Player"))
        viewer = player
        opponent_name = "Original_Rival"
        if mode == "team":
            opponent_name = "Original Rival Team"
            viewer = sign_in(app, create_user("Remaining_Member"))
            post(rival, "/teams/new", name=opponent_name, game_id=game)
            post(player, "/teams/new", name="Remaining Team", game_id=game)
            team = db.one("SELECT id FROM teams WHERE name='Remaining Team'")
            post(player, f"/teams/{team['id']}/members", nickname="Remaining_Member")
        detail = post(
            organizer, "/tournaments/new", **draft_fields(game, registration_type=mode, banner=image())
        ).location
        tournament_id = int(detail.rsplit("/", 1)[1])
        post(organizer, f"{detail}/publish")
        for entrant in (rival, player):
            post(entrant, f"{detail}/apply")
        for registration in db.all(
            "SELECT id FROM registrations WHERE tournament_id=%s", (tournament_id,)
        ):
            post(organizer, f"/registrations/{registration['id']}/approve")
        post(organizer, f"{detail}/generate-bracket")
        match = db.one("SELECT id FROM matches WHERE tournament_id=%s", (tournament_id,))
        post(organizer, f"/matches/{match['id']}/result", score1=1, score2=3)
        assert (
            db.one("SELECT status FROM tournaments WHERE id=%s", (tournament_id,))["status"]
            == "completed"
        )

        if mode == "solo":
            post(rival, "/profile", nickname="Renamed_Rival", email="Original_Rival@example.test")
            assert opponent_name in viewer.get("/my/matches").text
        post(administrator, f"/admin/users/{rival_id}/delete")
        assert not db.one("SELECT id FROM users WHERE id=%s", (rival_id,))
        history = viewer.get("/my/matches").text
        assert opponent_name in history
        assert "3 : 1" in history
