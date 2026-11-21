from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from pymysql.err import IntegrityError

from . import db
from .security import role_required
from .uploads import uploaded

bp = Blueprint("teams", __name__)
TEAM = """SELECT t.*,ga.name AS game_name,ga.team_size_min,ga.team_size_max
          FROM teams t JOIN games ga ON ga.id=t.game_id"""


@bp.get("/team")
@role_required("player")
def team():
    current_team = db.one(
        TEAM + " JOIN team_members m ON m.team_id=t.id WHERE m.user_id=%s", (g.user["id"],)
    )
    members = []
    if current_team:
        members = db.all(
            """SELECT u.id,u.nickname,m.role FROM team_members m JOIN users u ON u.id=m.user_id
                            WHERE m.team_id=%s ORDER BY m.role, u.nickname""",
            (current_team["id"],),
        )
    return render_template("team.html", team=current_team, members=members)


@bp.route("/teams/new", methods=["GET", "POST"])
@role_required("player")
def create():
    user_id = g.user["id"]
    if db.one("SELECT id FROM team_members WHERE user_id=%s", (user_id,)):
        flash("Вы уже состоите в команде.", "error")
        return redirect(url_for("teams.team"))
    form = {}
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        game_id = request.form.get("game_id", type=int)
        form = {"name": name, "game_id": game_id}
        try:
            if not 1 <= len(name) <= 120:
                raise ValueError("Введите название команды длиной до 120 символов.")
            with uploaded(request.files.get("logo")) as logo, db.transaction():
                game = db.one(
                    "SELECT id FROM games WHERE id=%s AND format='team' FOR UPDATE", (game_id,)
                )
                if not game:
                    raise ValueError("Выберите командную игру из списка.")
                team_id = db.execute(
                    "INSERT INTO teams (name,captain_id,game_id,logo) VALUES (%s,%s,%s,%s)",
                    (name, user_id, game_id, logo),
                )
                db.execute(
                    "INSERT INTO team_members (team_id,user_id,role) VALUES (%s,%s,'captain')",
                    (team_id, user_id),
                )
        except ValueError as error:
            flash(str(error), "error")
        except IntegrityError as error:
            if error.args[0] != 1062:
                raise
            flash("Название команды занято или вы уже состоите в команде.", "error")
        else:
            flash("Команда создана. Добавьте игроков в состав.", "success")
            return redirect(url_for("teams.team"))
    games = db.all(
        "SELECT id,name,team_size_min,team_size_max FROM games WHERE format='team' ORDER BY name"
    )
    return render_template("team_form.html", form=form, games=games)


def owned_team(team_id):
    current_team = db.one(TEAM + " WHERE t.id=%s FOR UPDATE", (team_id,))
    if not current_team:
        abort(404)
    if current_team["captain_id"] != g.user["id"]:
        abort(403)
    return current_team


@bp.post("/teams/<int:team_id>/members")
@role_required("player")
def add_member(team_id):
    nickname = request.form.get("nickname", "").strip()
    try:
        with db.transaction():
            current_team = owned_team(team_id)
            size = db.one("SELECT COUNT(*) AS n FROM team_members WHERE team_id=%s", (team_id,))["n"]
            if size >= current_team["team_size_max"]:
                raise ValueError("В команде нет свободных мест.")
            user = db.one("SELECT id FROM users WHERE nickname=%s AND role='player'", (nickname,))
            if not user:
                raise ValueError("Игрок с таким никнеймом не найден.")
            db.execute(
                "INSERT INTO team_members (team_id,user_id,role) VALUES (%s,%s,'player')",
                (team_id, user["id"]),
            )
    except ValueError as error:
        flash(str(error), "error")
    except IntegrityError as error:
        if error.args[0] != 1062:
            raise
        flash("Этот игрок уже состоит в команде.", "error")
    else:
        flash("Игрок добавлен в команду.", "success")
    return redirect(url_for("teams.team"))


@bp.post("/teams/<int:team_id>/members/<int:user_id>/remove")
@role_required("player")
def remove_member(team_id, user_id):
    with db.transaction():
        current_team = owned_team(team_id)
        member = db.one(
            "SELECT role FROM team_members WHERE team_id=%s AND user_id=%s", (team_id, user_id)
        )
        if not member:
            abort(404)
        size = db.one("SELECT COUNT(*) AS n FROM team_members WHERE team_id=%s", (team_id,))["n"]
        if member["role"] == "captain":
            flash("Капитана нельзя исключить из состава.", "error")
        elif size <= current_team["team_size_min"]:
            flash(
                "После исключения игроков в составе будет меньше минимального размера команды.", "error"
            )
        else:
            db.execute("DELETE FROM team_members WHERE team_id=%s AND user_id=%s", (team_id, user_id))
            flash("Игрок исключён из команды.", "success")
    return redirect(url_for("teams.team"))
