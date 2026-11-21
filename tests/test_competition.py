"""FR-018–022: schedules, atomic mutations and final standings."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from threading import Barrier
from uuid import uuid4

import pytest
from flask import g
from werkzeug.exceptions import NotFound

from app import competition as comp
from app import db
from app.brackets import schedule


@pytest.mark.parametrize("size", range(2, 34))
@pytest.mark.fr("FR-019")
def test_fr019_all_pairs_once(size):
    players = list(range(1, size + 1))
    rounds = schedule(players, "round_robin")
    seen = []
    for pairs in rounds:
        participants = [player for pair in pairs for player in pair]
        assert len(set(participants)) == len(participants)
        seen.extend(tuple(sorted(pair)) for pair in pairs)
    assert sorted(seen) == list(combinations(players, 2))


@pytest.mark.parametrize("size", range(2, 34))
@pytest.mark.fr("FR-018")
def test_fr018_byes_and_round_count(size):
    rounds = schedule(range(1, size + 1), "single_elimination")
    assert len(rounds) == (size - 1).bit_length()
    assert len(rounds[-1]) == 1
    assert all(a or b for a, b in rounds[0])
    assert sorted(p for pair in rounds[0] for p in pair if p) == list(range(1, size + 1))


def make_tournament(size, fmt):
    tag = uuid4().hex[:8]

    def user(i, role):
        name = f"u{tag}{i}"
        return db.execute(
            "INSERT INTO users(nickname,email,password_hash,role) VALUES(%s,%s,%s,%s)",
            (name, f"{name}@example.test", "unused", role),
        )

    with db.transaction():
        owner = user(0, "organizer")
        game = db.execute(
            "INSERT INTO games(name,team_size_min,team_size_max,format) VALUES(%s,1,1,'solo')",
            ("Game " + tag,),
        )
        tid = db.execute(
            """INSERT INTO tournaments(title,description,game_id,organizer_id,format,
               registration_type,status,max_participants,start_date,end_date)
               VALUES(%s,'Description',%s,%s,%s,'solo','published',%s,'2030-01-01','2030-01-02')""",
            ("Cup " + tag, game, owner, fmt, size),
        )
        for i in range(1, size + 1):
            db.execute(
                "INSERT INTO registrations(tournament_id,user_id,status) VALUES(%s,%s,'approved')",
                (tid, user(i, "player")),
            )
    g.user = {"id": owner, "role": "organizer"}
    return tid, owner


def matches(tid):
    return db.all(
        "SELECT * FROM matches WHERE tournament_id=%s ORDER BY round_number,match_number", (tid,)
    )


def tournament(tid):
    return db.one("SELECT * FROM tournaments WHERE id=%s", (tid,))


@pytest.mark.fr("FR-018", "FR-020", "FR-021", "FR-022")
def test_fr018_020_021_022_knockout(app):
    with app.app_context():
        tid, _ = make_tournament(5, "single_elimination")
        comp.generate_bracket(tid)
        with pytest.raises(comp.CompetitionError):
            comp.generate_bracket(tid)
        with pytest.raises(comp.CompetitionError):
            comp.finish_tournament(tid)
        initial = matches(tid)
        assert Counter(m["status"] for m in initial) == {"walkover": 3, "scheduled": 4}
        with pytest.raises(comp.CompetitionError):
            comp.record_result(initial[-1]["id"], {"score1": "2", "score2": "1"})
        for original in initial:
            if original["status"] == "walkover":
                continue
            mid = original["id"]
            with pytest.raises(comp.CompetitionError):
                comp.record_result(mid, {"score1": "2", "score2": "2"})
            comp.record_result(mid, {"score1": "1", "score2": "2"})
            with pytest.raises(comp.CompetitionError):
                comp.record_result(mid, {"score1": "3", "score2": "1"})
        assert tournament(tid)["status"] == "completed"
        table = comp.bracket_context(tournament(tid))["standings"]
        assert [row["rank"] for row in table] == [1, 2, 3, 3, 5]
        assert table[0]["registration_id"] == matches(tid)[-1]["winner_registration_id"]


@pytest.mark.fr("FR-019", "FR-020", "FR-022")
def test_fr019_020_022_round_robin(app):
    with app.app_context():
        tid, owner = make_tournament(3, "round_robin")
        g.user = {"id": owner + 1, "role": "organizer"}
        with pytest.raises(NotFound):
            comp.generate_bracket(tid)
        g.user["id"] = owner
        comp.generate_bracket(tid)
        games = matches(tid)
        assert all(m["next_match_id"] is None for m in games)
        first = games[0]
        for bad in ("-1", "3.5", "", "2147483648"):
            with pytest.raises(comp.CompetitionError):
                comp.record_result(first["id"], {"score1": bad, "score2": "0"})
        with pytest.raises(comp.CompetitionError):
            comp.record_result(first["id"], {"mode": "walkover", "winner_registration_id": "-1"})
        comp.record_result(first["id"], {"score1": "0", "score2": "0"})
        tied = {first["registration1_id"], first["registration2_id"]}
        for match in games[1:]:
            (winner,) = tied.intersection([match["registration1_id"], match["registration2_id"]])
            comp.record_result(match["id"], {"mode": "walkover", "winner_registration_id": str(winner)})
        assert tournament(tid)["status"] == "completed"
        table = comp.bracket_context(tournament(tid))["standings"]
        assert [row["rank"] for row in table] == [1, 1, 3]
        assert [row["wins"] for row in table] == [1, 1, 0]
        assert all(row["played"] == 2 for row in table)


@pytest.mark.fr("FR-021")
def test_fr021_rollback_on_advance_error(app, monkeypatch):
    with app.app_context():
        tid, _ = make_tournament(4, "single_elimination")
        comp.generate_bracket(tid)
        before = matches(tid)

        def fail(*args):
            raise RuntimeError("simulated failure while promoting winner")

        monkeypatch.setattr(comp, "_advance", fail)
        with pytest.raises(RuntimeError):
            comp.record_result(before[0]["id"], {"score1": "1", "score2": "0"})
        assert matches(tid) == before
        assert tournament(tid)["status"] == "in_progress"


@pytest.mark.fr("FR-018")
def test_fr018_simultaneous_generation(app):
    with app.app_context():
        tid, owner = make_tournament(2, "single_elimination")
    barrier = Barrier(2)

    def generate(_):
        with app.app_context():
            g.user = {"id": owner}
            barrier.wait(timeout=10)
            try:
                comp.generate_bracket(tid)
                return True
            except comp.CompetitionError:
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(generate, range(2))) == [False, True]
    with app.app_context():
        assert len(matches(tid)) == 1


@pytest.mark.fr("FR-020", "FR-021", "FR-022")
def test_competing_results_close_final_once(app):
    with app.app_context():
        tid, owner = make_tournament(2, "single_elimination")
        comp.generate_bracket(tid)
        final = matches(tid)[0]
    barrier = Barrier(2)

    def submit(score):
        with app.app_context():
            g.user = {"id": owner}
            barrier.wait(timeout=10)
            try:
                comp.record_result(final["id"], {"score1": str(score), "score2": str(1 - score)})
                return score
            except comp.CompetitionError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = [score for score in pool.map(submit, [0, 1]) if score is not None]
    assert len(accepted) == 1
    with app.app_context():
        (played,) = matches(tid)
        assert played["score1"] == accepted[0]
        assert played["score2"] == 1 - accepted[0]
        assert tournament(tid)["status"] == "completed"


@pytest.mark.fr("FR-018", "FR-020", "FR-022")
def test_competition_http(client, app):
    with app.app_context():
        tid, owner = make_tournament(2, "single_elimination")
    with client.session_transaction() as session:
        session["user_id"] = owner
        session["csrf_token"] = "test-csrf"
    token = {"csrf_token": "test-csrf"}
    generate_url = f"/tournaments/{tid}/generate-bracket"
    assert client.post(generate_url).status_code == 403
    generated = client.post(generate_url, data=token)
    assert generated.status_code == 302
    assert generated.headers["Location"].endswith(f"/tournaments/{tid}")
    with app.app_context():
        final = matches(tid)[0]
    assert (
        client.post(
            f"/matches/{final['id']}/result", data={**token, "score1": "0", "score2": "1"}
        ).status_code
        == 302
    )
    with app.app_context():
        assert tournament(tid)["status"] == "completed"
        assert matches(tid)[0]["winner_registration_id"] == final["registration2_id"]
