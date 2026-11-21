from flask import Blueprint, abort, current_app, g, render_template, request

from . import db
from .competition import bracket_context

bp = Blueprint("public", __name__)
SORTS = {
    "new_desc": "t.created_at DESC,t.id DESC",
    "new_asc": "t.created_at ASC,t.id ASC",
    "title_asc": "t.title ASC,t.id ASC",
    "title_desc": "t.title DESC,t.id DESC",
}
CARD = """SELECT t.id,t.title,t.description,t.status,t.start_date,t.end_date,t.created_at,t.updated_at,
    t.game_id,t.banner,t.max_participants,t.format,t.registration_type,g.name AS game_name,
    (SELECT COUNT(*) FROM registrations r WHERE r.tournament_id=t.id
     AND r.status='approved') AS approved_count FROM tournaments t
     JOIN games g ON g.id=t.game_id"""


def catalog(history):
    games = db.all("SELECT id,name FROM games ORDER BY name")
    conditions = ["t.status='completed'" if history else "t.status IN ('published','in_progress')"]
    args = []
    q = request.args.get("q", "").strip() if not history else ""
    game = request.args.get("game", 0, type=int)
    if game:
        conditions.append("t.game_id=%s")
        args.append(game)
    if q:
        conditions.append("(t.title LIKE %s ESCAPE '!' OR t.description LIKE %s ESCAPE '!')")
        pattern = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        args.extend([pattern, pattern])
    where = " WHERE " + " AND ".join(conditions)
    total = db.one("SELECT COUNT(*) AS n FROM tournaments t" + where, args)["n"]
    size = current_app.config["PER_PAGE"]
    pages = max(1, (total + size - 1) // size)
    page = min(pages, max(1, request.args.get("page", 1, type=int)))
    sort = request.args.get("sort", "new_desc")
    if sort not in SORTS:
        sort = "new_desc"
    order = "t.updated_at DESC,t.id DESC" if history else SORTS[sort]
    rows = db.all(
        CARD + where + " ORDER BY " + order + " LIMIT %s OFFSET %s", (*args, size, (page - 1) * size)
    )
    return render_template(
        "catalog.html",
        tournaments=rows,
        games=games,
        q=q,
        game=game,
        sort=sort,
        pages=pages,
        page=page,
        total=total,
        history=history,
    )


@bp.get("/")
def index():
    return catalog(False)


@bp.get("/history")
def history():
    return catalog(True)


def my_team():
    return db.one(
        """SELECT t.*, m.role AS member_role FROM team_members m
                     JOIN teams t ON t.id=m.team_id WHERE m.user_id=%s""",
        (g.user["id"],),
    )


@bp.get("/tournaments/<int:tournament_id>")
def detail(tournament_id):
    tournament = db.one(
        """SELECT t.*,u.nickname AS organizer_name,g.name AS game_name
                           FROM tournaments t JOIN games g ON g.id=t.game_id
                           JOIN users u ON u.id=t.organizer_id WHERE t.id=%s""",
        (tournament_id,),
    )
    if not tournament:
        abort(404)
    is_owner = bool(
        g.user and g.user["role"] == "organizer" and tournament["organizer_id"] == g.user["id"]
    )
    if tournament["status"] in {"draft", "cancelled"} and not is_owner:
        abort(404)
    participants = db.all(
        """SELECT r.id,COALESCE(tm.name,u.nickname) AS name
                              FROM registrations r LEFT JOIN teams tm ON tm.id=r.team_id
                              LEFT JOIN users u ON u.id=r.user_id WHERE r.tournament_id=%s
                              AND r.status='approved' ORDER BY r.id""",
        (tournament_id,),
    )
    context = bracket_context(tournament)
    if tournament["status"] == "completed" and tournament.get("results_snapshot"):
        participants = [
            {"id": row["registration_id"], "name": row["name"]} for row in context["standings"]
        ]
    tournament["approved_count"] = len(participants)
    registration = thread = None
    can_apply = False
    if g.user and g.user["role"] == "player":
        team = my_team()
        registration = db.one(
            """SELECT * FROM registrations WHERE tournament_id=%s
                                 AND (user_id=%s OR team_id=%s) ORDER BY id DESC LIMIT 1""",
            (tournament_id, g.user["id"], team["id"] if team else None),
        )
        can_represent = tournament["registration_type"] == "solo" or bool(
            team and team["member_role"] == "captain" and team["game_id"] == tournament["game_id"]
        )
        can_apply = (
            can_represent
            and tournament["status"] == "published"
            and (not registration or registration["status"] in {"withdrawn", "rejected"})
        )
        if registration:
            registration["can_withdraw"] = can_represent and registration["status"] == "pending"
        thread = db.one(
            "SELECT id FROM chat_threads WHERE tournament_id=%s AND participant_user_id=%s",
            (tournament_id, g.user["id"]),
        )
    return render_template(
        "detail.html",
        tournament=tournament,
        is_owner=is_owner,
        participants=participants,
        registration=registration,
        can_apply=can_apply,
        thread_id=thread and thread["id"],
        has_registrations=bool(
            is_owner
            and db.one("SELECT id FROM registrations WHERE tournament_id=%s LIMIT 1", (tournament_id,))
        ),
        can_finish=is_owner
        and tournament["status"] == "in_progress"
        and bool(context["rounds"])
        and all(m["status"] in ("played", "walkover") for r in context["rounds"] for m in r),
        **context,
    )
