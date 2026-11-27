"""FR-018–022. Each mutation locks the same tournament before changing its matches."""

import json
import re
from itertools import groupby

from flask import Blueprint, abort, current_app, flash, g, redirect, request, url_for
from pymysql import MySQLError

from . import db
from .brackets import round_dates, schedule, standings
from .security import role_required

bp = Blueprint("competition", __name__)


class CompetitionError(ValueError):
    """A request conflicts with the present tournament state or result rules."""


def _owned_tournament(tournament_id):
    tournament = db.one(
        "SELECT * FROM tournaments WHERE id=%s AND organizer_id=%s FOR UPDATE",
        (tournament_id, g.user["id"]),
    )
    if tournament is None:
        abort(404)
    return tournament


def _advance(match_number, next_match_id, winner):
    if next_match_id is None:
        return
    # Both children can finish in either order. The odd-numbered child owns slot 1.
    slot = "registration1_id" if match_number % 2 else "registration2_id"
    db.execute(f"UPDATE matches SET {slot}=%s WHERE id=%s", (winner, next_match_id))


def generate_bracket(tournament_id):
    with db.transaction():
        tournament = _owned_tournament(tournament_id)
        _generate_bracket(tournament)


def _generate_bracket(tournament):
    tournament_id = tournament["id"]
    if tournament["status"] not in ("draft", "published"):
        raise CompetitionError("Сетку можно создать только до начала турнира.")
    if db.one("SELECT id FROM matches WHERE tournament_id=%s LIMIT 1", (tournament_id,)):
        raise CompetitionError("Сетка уже существует.")
    entries = db.all(
        """SELECT id FROM registrations WHERE tournament_id=%s AND status='approved'
           ORDER BY id FOR UPDATE""",
        (tournament_id,),
    )
    if not 2 <= len(entries) <= tournament["max_participants"]:
        raise CompetitionError("Проверьте число одобренных участников: от двух до лимита турнира.")
    rounds = schedule([row["id"] for row in entries], tournament["format"])
    dates = round_dates(tournament["start_date"], tournament["end_date"], len(rounds))
    knockout = tournament["format"] == "single_elimination"
    parents = []
    for round_idx in reversed(range(len(rounds))):
        ids = []
        for pair_idx, (a, b) in enumerate(rounds[round_idx]):
            parent = parents[pair_idx // 2] if knockout and parents else None
            bye = knockout and round_idx == 0 and (a is None or b is None)
            winner = (a or b) if bye else None
            match_id = db.execute(
                """INSERT INTO matches
                   (tournament_id,round_number,match_number,registration1_id,registration2_id,
                    next_match_id,status,winner_registration_id,scheduled_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    tournament_id,
                    round_idx + 1,
                    pair_idx + 1,
                    a,
                    b,
                    parent,
                    "walkover" if bye else "scheduled",
                    winner,
                    dates[round_idx],
                ),
            )
            ids.append(match_id)
            if bye:
                _advance(pair_idx + 1, parent, winner)
        parents = ids
    db.execute("UPDATE tournaments SET status='in_progress' WHERE id=%s", (tournament_id,))


def _score(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,10}", raw) or int(raw) > 2147483647:
        raise CompetitionError("Введите целый неотрицательный счёт для каждой стороны.")
    return int(raw)


def _completion_ready(tournament_id):
    row = db.one(
        """SELECT COUNT(*) AS total, SUM(status IN ('played','walkover')) AS finished
           FROM matches WHERE tournament_id=%s""",
        (tournament_id,),
    )
    return row["total"] > 0 and row["finished"] == row["total"]


def _complete(tournament_id, completed_at=None):
    tournament = db.one("SELECT * FROM tournaments WHERE id=%s", (tournament_id,))
    snapshot = json.dumps(bracket_context(tournament), ensure_ascii=False, default=str)
    db.execute(
        """UPDATE tournaments SET status='completed',completed_at=COALESCE(%s,CURRENT_TIMESTAMP),
           results_snapshot=%s WHERE id=%s""",
        (completed_at, snapshot, tournament_id),
    )


def record_result(match_id, form):
    # Resolve immutable parent ID before the transaction so no old consistent-read
    # snapshot can hide a result committed while this request waited for the lock.
    ref = db.one("SELECT tournament_id FROM matches WHERE id=%s", (match_id,))
    if not ref:
        abort(404)
    tournament_id = ref["tournament_id"]
    with db.transaction():
        tournament = _owned_tournament(tournament_id)
        _record_result(tournament, match_id, form)
    return tournament_id


def _record_result(tournament, match_id, form, completed_at=None):
    tournament_id = tournament["id"]
    match = db.one("SELECT * FROM matches WHERE id=%s FOR UPDATE", (match_id,))
    if not match:
        abort(404)
    if tournament["status"] != "in_progress" or match["status"] != "scheduled":
        raise CompetitionError("Этот матч закрыт для ввода результата.")
    a, b = match["registration1_id"], match["registration2_id"]
    if a is None or b is None:
        raise CompetitionError("Дождитесь определения обоих участников матча.")
    mode = form.get("mode", "played")
    if mode == "walkover":
        choice = form.get("winner_registration_id")
        if choice not in (str(a), str(b)):
            raise CompetitionError("Укажите участника, получившего техническую победу.")
        winner = int(choice)
        score1, score2 = (1, 0) if a == winner else (0, 1)
    elif mode == "played":
        score1, score2 = _score(form.get("score1")), _score(form.get("score2"))
        winner = a if score1 > score2 else b if score2 > score1 else None
        if winner is None and tournament["format"] == "single_elimination":
            raise CompetitionError("В матче на выбывание ничья невозможна.")
    else:
        raise CompetitionError("Неизвестный способ завершения матча.")
    db.execute(
        """UPDATE matches SET score1=%s,score2=%s,winner_registration_id=%s,status=%s
           WHERE id=%s""",
        (score1, score2, winner, mode, match_id),
    )
    if tournament["format"] == "single_elimination":
        _advance(match["match_number"], match["next_match_id"], winner)
    if _completion_ready(tournament_id):
        _complete(tournament_id, completed_at)


def play_demo_tournament(tournament_id, scores, completed_at):
    """Play a demo draw inside the caller's transaction."""
    _generate_bracket(_owned_tournament(tournament_id))
    tournament = _owned_tournament(tournament_id)
    matches = db.all(
        "SELECT id FROM matches WHERE tournament_id=%s ORDER BY round_number,match_number",
        (tournament_id,),
    )
    for match, (score1, score2) in zip(matches, scores, strict=True):
        _record_result(tournament, match["id"], {"score1": str(score1), "score2": str(score2)}, completed_at)


def finish_tournament(tournament_id):
    with db.transaction():
        tournament = _owned_tournament(tournament_id)
        if tournament["status"] != "in_progress" or not _completion_ready(tournament_id):
            raise CompetitionError("Завершение доступно после всех матчей действующего турнира.")
        _complete(tournament_id)


def bracket_context(tournament):
    if tournament["status"] == "completed" and tournament.get("results_snapshot"):
        return json.loads(tournament["results_snapshot"])
    if tournament["status"] not in ("in_progress", "completed"):
        return {"rounds": [], "standings": []}
    entries = db.all(
        """SELECT r.id, COALESCE(t.name,u.nickname) AS name FROM registrations r
           LEFT JOIN teams t ON t.id=r.team_id LEFT JOIN users u ON u.id=r.user_id
           WHERE r.tournament_id=%s AND r.status='approved' ORDER BY r.id""",
        (tournament["id"],),
    )
    names = {row["id"]: row["name"] for row in entries}
    matches = db.all(
        "SELECT * FROM matches WHERE tournament_id=%s ORDER BY round_number,match_number",
        (tournament["id"],),
    )
    for match in matches:
        match["name1"] = names.get(match["registration1_id"])
        match["name2"] = names.get(match["registration2_id"])
    rounds = [list(group) for _, group in groupby(matches, key=lambda m: m["round_number"])]
    return {"rounds": rounds, "standings": standings(entries, matches, tournament["format"])}


@bp.post("/tournaments/<int:tournament_id>/generate-bracket")
@role_required("organizer")
def generate(tournament_id):
    try:
        generate_bracket(tournament_id)
        flash("Сетка создана. Турнир начался.", "success")
    except CompetitionError as exc:
        flash(str(exc), "error")
    except MySQLError:
        current_app.logger.exception("Tournament update failed")
        flash("Не удалось сохранить изменения. Повторите попытку.", "error")
    return redirect(url_for("public.detail", tournament_id=tournament_id))


@bp.post("/matches/<int:match_id>/result")
@role_required("organizer")
def result(match_id):
    try:
        tournament_id = record_result(match_id, request.form)
        flash("Результат сохранён.", "success")
    except (CompetitionError, MySQLError) as exc:
        if isinstance(exc, CompetitionError):
            flash(str(exc), "error")
        else:
            current_app.logger.exception("Match result failed")
            flash("Не удалось сохранить результат. Повторите попытку.", "error")
        match = db.one("SELECT tournament_id FROM matches WHERE id=%s", (match_id,))
        if not match:
            abort(404)
        tournament_id = match["tournament_id"]
    return redirect(url_for("public.detail", tournament_id=tournament_id))


@bp.post("/tournaments/<int:tournament_id>/finish")
@role_required("organizer")
def finish(tournament_id):
    try:
        finish_tournament(tournament_id)
        flash("Турнир завершён.", "success")
    except CompetitionError as exc:
        flash(str(exc), "error")
    except MySQLError:
        current_app.logger.exception("Tournament update failed")
        flash("Не удалось сохранить изменения. Повторите попытку.", "error")
    return redirect(url_for("public.detail", tournament_id=tournament_id))
