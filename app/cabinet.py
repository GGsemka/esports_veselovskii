from flask import Blueprint, g, render_template

from . import db
from .competition import bracket_context
from .security import role_required

bp = Blueprint("cabinet", __name__)


@bp.get("/my/tournaments")
@role_required("player")
def tournaments():
    user_id = g.user["id"]
    rows = db.all(
        """SELECT t.id,t.title,t.status,ga.name AS game_name,
                            r.status AS registration_status,r.rejection_reason,ct.id AS thread_id
                     FROM registrations r JOIN tournaments t ON t.id=r.tournament_id
                     JOIN games ga ON ga.id=t.game_id
                     LEFT JOIN team_members tm ON tm.team_id=r.team_id AND tm.user_id=%s
                     LEFT JOIN chat_threads ct ON ct.tournament_id=t.id AND ct.participant_user_id=%s
                     WHERE (tm.id IS NOT NULL OR r.user_id=%s)
                     AND t.status NOT IN ('draft','cancelled')
                     AND NOT EXISTS (
                         SELECT 1 FROM registrations newer WHERE newer.tournament_id=r.tournament_id
                         AND (newer.team_id=r.team_id OR newer.user_id=r.user_id) AND newer.id>r.id
                     )
                     ORDER BY r.updated_at DESC,r.id DESC""",
        (user_id, user_id, user_id),
    )
    return render_template("my_tournaments.html", tournaments=rows)


@bp.get("/my/matches")
@role_required("player")
def matches():
    rows = db.all(
        """SELECT m.id,m.tournament_id,t.title,m.scheduled_at,m.status,t.status AS tournament_status,
                            mine.id AS registration_id,
                            CASE WHEN mine.id=m.registration1_id THEN m.score1 ELSE m.score2 END AS score_for,
                            CASE WHEN mine.id=m.registration1_id THEN m.score2 ELSE m.score1 END AS score_against,
                            COALESCE(opponent_team.name,opponent_user.nickname) AS opponent
                     FROM registrations mine
                     LEFT JOIN team_members tm ON tm.team_id=mine.team_id AND tm.user_id=%s
                     JOIN matches m ON mine.id IN (m.registration1_id,m.registration2_id)
                     JOIN tournaments t ON t.id=m.tournament_id
                     LEFT JOIN registrations opponent ON opponent.id=
                         CASE WHEN mine.id=m.registration1_id THEN m.registration2_id ELSE m.registration1_id END
                     LEFT JOIN teams opponent_team ON opponent_team.id=opponent.team_id
                     LEFT JOIN users opponent_user ON opponent_user.id=opponent.user_id
                     WHERE (tm.id IS NOT NULL OR mine.user_id=%s)
                     AND t.status NOT IN ('draft','cancelled')
                     ORDER BY m.scheduled_at IS NULL,m.scheduled_at,m.round_number,m.match_number,m.id""",
        (g.user["id"], g.user["id"]),
    )
    completed_ids = sorted(
        {row["tournament_id"] for row in rows if row["tournament_status"] == "completed"}
    )
    saved_matches = {}
    if completed_ids:
        placeholders = ",".join(["%s"] * len(completed_ids))
        snapshots = db.all(
            f"""SELECT id,status,results_snapshot FROM tournaments
                WHERE id IN ({placeholders}) AND results_snapshot IS NOT NULL""",
            completed_ids,
        )
        saved_matches = {
            match["id"]: match
            for snapshot in snapshots
            for round_matches in bracket_context(snapshot)["rounds"]
            for match in round_matches
        }
    for row in rows:
        saved = saved_matches.get(row["id"])
        if saved:
            own_side, opponent_side = (
                (1, 2) if row["registration_id"] == saved["registration1_id"] else (2, 1)
            )
            row["opponent"] = saved[f"name{opponent_side}"]
            row["score_for"] = saved[f"score{own_side}"]
            row["score_against"] = saved[f"score{opponent_side}"]
    upcoming = [row for row in rows if row["status"] == "scheduled"]
    past = [row for row in reversed(rows) if row["status"] != "scheduled"]
    return render_template("my_matches.html", upcoming=upcoming, past=past)
