from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db
from .public import CARD
from .security import role_required
from .uploads import remove_upload, uploaded

bp = Blueprint("organizer", __name__)


def owned(tournament_id, lock=False):
    row = db.one(
        "SELECT * FROM tournaments WHERE id=%s AND organizer_id=%s" + (" FOR UPDATE" if lock else ""),
        (tournament_id, g.user["id"]),
    )
    if not row:
        abort(404)
    return row


def detail_redirect(tournament_id):
    return redirect(url_for("public.detail", tournament_id=tournament_id))


def validate_tournament(form, games):
    title, description = (form.get(k, "").strip() for k in ("title", "description"))
    if not 5 <= len(title) <= 120:
        raise ValueError("Название должно содержать от 5 до 120 символов.")
    if not 30 <= len(description) <= 2000:
        raise ValueError("Описание должно содержать от 30 до 2000 символов.")
    game = next((g for g in games if str(g["id"]) == str(form.get("game_id"))), None)
    if not game:
        raise ValueError("Выберите дисциплину.")
    reg_type, fmt = form.get("registration_type"), form.get("format")
    if game["format"] != reg_type:
        raise ValueError("Вид участия должен соответствовать дисциплине.")
    if fmt not in {"round_robin", "single_elimination"}:
        raise ValueError("Выберите систему проведения.")
    try:
        capacity = int(form.get("max_participants") or 0)
        start, end = (date.fromisoformat(str(form.get(k))) for k in ("start_date", "end_date"))
    except (ValueError, TypeError):
        raise ValueError("Проверьте даты и число участников.") from None
    if not 2 <= capacity <= 64 or start >= end:
        raise ValueError("Допустимо 2–64 участника. Окончание должно быть позже начала.")
    return title, description, game["id"], fmt, reg_type, capacity, start, end


def editor(tournament_id=None):
    record = owned(tournament_id) if tournament_id else {}
    if record and record["status"] != "draft":
        abort(409, "Редактировать можно только черновик.")
    games = db.all("SELECT * FROM games ORDER BY name")
    if request.method == "POST":
        try:
            fields = validate_tournament(request.form, games)
            replaced_banner = None
            with uploaded(request.files.get("banner")) as filename, db.transaction():
                if tournament_id:
                    row = owned(tournament_id, True)
                    if row["status"] != "draft":
                        abort(409)
                    if filename:
                        replaced_banner = row["banner"]
                    db.execute(
                        """UPDATE tournaments SET title=%s,description=%s,game_id=%s,format=%s,
                               registration_type=%s,max_participants=%s,start_date=%s,end_date=%s,
                               banner=COALESCE(%s,banner) WHERE id=%s AND organizer_id=%s""",
                        (*fields, filename, tournament_id, g.user["id"]),
                    )
                else:
                    tournament_id = db.execute(
                        """INSERT INTO tournaments
                        (title,description,game_id,format,registration_type,max_participants,start_date,
                         end_date,banner,organizer_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (*fields, filename, g.user["id"]),
                    )
            remove_upload(replaced_banner)
            flash("Черновик сохранён.", "success")
            return detail_redirect(tournament_id)
        except ValueError as error:
            flash(str(error), "error")
            record = {**record, **request.form.to_dict()}
    return render_template("tournament_form.html", tournament=record, games=games)


@bp.route("/tournaments/new", methods=["GET", "POST"])
@role_required("organizer")
def new():
    return editor()


@bp.route("/tournaments/<int:tournament_id>/edit", methods=["GET", "POST"])
@role_required("organizer")
def edit(tournament_id):
    return editor(tournament_id)


@bp.get("/organizer/tournaments")
@role_required("organizer")
def mine():
    rows = db.all(
        CARD + " WHERE t.organizer_id=%s ORDER BY t.updated_at DESC,t.id DESC", (g.user["id"],)
    )
    return render_template("organizer.html", tournaments=rows)


@bp.post("/tournaments/<int:tournament_id>/publish")
@role_required("organizer")
def publish(tournament_id):
    try:
        with db.transaction():
            row = owned(tournament_id, True)
            if row["status"] != "draft" or not row["banner"]:
                raise ValueError("Для публикации нужен черновик с баннером.")
            validate_tournament(
                row, db.all("SELECT id,format FROM games WHERE id=%s", (row["game_id"],))
            )
            db.execute("UPDATE tournaments SET status='published' WHERE id=%s", (tournament_id,))
        flash("Регистрация открыта.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return detail_redirect(tournament_id)


@bp.post("/tournaments/<int:tournament_id>/unpublish")
@role_required("organizer")
def unpublish(tournament_id):
    with db.transaction():
        row = owned(tournament_id, True)
        if row["status"] != "published" or db.one(
            "SELECT id FROM registrations WHERE tournament_id=%s LIMIT 1", (tournament_id,)
        ):
            flash("Снять с публикации можно только турнир без заявок.", "error")
        else:
            db.execute("UPDATE tournaments SET status='draft' WHERE id=%s", (tournament_id,))
            flash("Турнир возвращён в черновики.", "success")
    return detail_redirect(tournament_id)


@bp.post("/tournaments/<int:tournament_id>/delete")
@role_required("organizer")
def delete(tournament_id):
    with db.transaction():
        row = owned(tournament_id, True)
        if row["status"] != "draft":
            flash("Удалить можно только черновик.", "error")
            return detail_redirect(tournament_id)
        db.execute("DELETE FROM tournaments WHERE id=%s AND organizer_id=%s", (tournament_id, g.user["id"]))
    remove_upload(row["banner"])
    flash("Черновик удалён.", "success")
    return redirect(url_for("organizer.mine"))


@bp.post("/tournaments/<int:tournament_id>/cancel")
@role_required("organizer")
def cancel(tournament_id):
    with db.transaction():
        row = owned(tournament_id, True)
        if row["status"] not in ("draft", "published", "in_progress"):
            flash("Этот турнир уже закрыт.", "error")
            return detail_redirect(tournament_id)
        db.execute(
            "UPDATE matches SET status='cancelled' WHERE tournament_id=%s AND status='scheduled'",
            (tournament_id,),
        )
        db.execute("UPDATE tournaments SET status='cancelled' WHERE id=%s", (tournament_id,))
    flash("Турнир отменён.", "success")
    return detail_redirect(tournament_id)


def approved_count(tournament_id):
    return db.one(
        "SELECT COUNT(*) AS n FROM registrations WHERE tournament_id=%s AND status='approved'",
        (tournament_id,),
    )["n"]


def eligible_team(team_id, tournament):
    team = db.one("SELECT id,game_id FROM teams WHERE id=%s FOR UPDATE", (team_id,))
    game = db.one("SELECT team_size_min,team_size_max FROM games WHERE id=%s", (tournament["game_id"],))
    count = db.one("SELECT COUNT(*) AS n FROM team_members WHERE team_id=%s", (team_id,))["n"]
    if not team or team["game_id"] != tournament["game_id"]:
        raise ValueError("Команда должна играть в выбранную дисциплину.")
    if not game["team_size_min"] <= count <= game["team_size_max"]:
        raise ValueError("Размер состава не соответствует требованиям дисциплины.")


@bp.post("/tournaments/<int:tournament_id>/apply")
@role_required("player")
def apply(tournament_id):
    try:
        with db.transaction():
            row = db.one("SELECT * FROM tournaments WHERE id=%s FOR UPDATE", (tournament_id,))
            if not row:
                abort(404)
            if row["status"] != "published":
                raise ValueError("Приём заявок закрыт.")
            user_id, team_id = g.user["id"], None
            if not db.one("SELECT id FROM users WHERE id=%s AND role='player' FOR UPDATE", (user_id,)):
                abort(403)
            if row["registration_type"] == "team":
                team = db.one(
                    """SELECT tm.team_id FROM team_members tm
                               WHERE tm.user_id=%s AND tm.role='captain'""",
                    (user_id,),
                )
                if not team:
                    abort(403)
                team_id, user_id = team["team_id"], None
                eligible_team(team_id, row)
            elif db.one(
                """SELECT tm.id FROM team_members tm JOIN teams t ON t.id=tm.team_id
                   WHERE tm.user_id=%s AND t.game_id=%s""",
                (user_id, row["game_id"]),
            ):
                raise ValueError("Этот турнир для игроков без команды по данной дисциплине.")
            if db.one(
                """SELECT id FROM registrations WHERE tournament_id=%s AND
                         (user_id=%s OR team_id=%s) AND status IN ('pending','approved')""",
                (tournament_id, user_id, team_id),
            ):
                raise ValueError("Активная заявка уже есть.")
            if approved_count(tournament_id) >= row["max_participants"]:
                raise ValueError("Достигнут лимит участников.")
            db.execute(
                "INSERT INTO registrations(tournament_id,user_id,team_id) VALUES(%s,%s,%s)",
                (tournament_id, user_id, team_id),
            )
            db.execute(
                """INSERT INTO chat_threads(tournament_id,participant_user_id) VALUES(%s,%s)
                        ON DUPLICATE KEY UPDATE id=id""",
                (tournament_id, g.user["id"]),
            )
        flash("Заявка отправлена организатору.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return detail_redirect(tournament_id)


@bp.post("/registrations/<int:registration_id>/withdraw")
@role_required("player")
def withdraw(registration_id):
    ref = db.one("SELECT tournament_id FROM registrations WHERE id=%s", (registration_id,))
    if not ref:
        abort(404)
    with db.transaction():
        tournament = db.one(
            "SELECT status FROM tournaments WHERE id=%s FOR UPDATE", (ref["tournament_id"],)
        )
        row = db.one(
            """SELECT r.tournament_id,r.status FROM registrations r LEFT JOIN teams tm
                        ON tm.id=r.team_id WHERE r.id=%s AND (r.user_id=%s OR tm.captain_id=%s)
                        FOR UPDATE""",
            (registration_id, g.user["id"], g.user["id"]),
        )
        if not row:
            abort(404)
        if row["status"] == "pending" and tournament["status"] == "published":
            db.execute(
                "UPDATE registrations SET status='withdrawn' WHERE id=%s AND status='pending'",
                (registration_id,),
            )
            flash("Заявка отозвана.", "success")
        else:
            flash("Отозвать можно только ожидающую заявку.", "error")
    return detail_redirect(row["tournament_id"])


@bp.get("/tournaments/<int:tournament_id>/registrations")
@role_required("organizer")
def registrations(tournament_id):
    tournament = owned(tournament_id)
    rows = db.all(
        """SELECT r.*,COALESCE(tm.name,u.nickname) AS name FROM registrations r
                     LEFT JOIN teams tm ON r.team_id=tm.id LEFT JOIN users u ON u.id=r.user_id
                     WHERE r.tournament_id=%s ORDER BY r.status='pending' DESC,r.id DESC""",
        (tournament_id,),
    )
    return render_template("registrations.html", tournament=tournament, registrations=rows)


def moderate(registration_id, approve):
    found = db.one("SELECT tournament_id FROM registrations WHERE id=%s", (registration_id,))
    if not found:
        abort(404)
    tid = found["tournament_id"]
    try:
        with db.transaction():
            tournament = owned(tid, True)
            row = db.one(
                "SELECT * FROM registrations WHERE id=%s AND tournament_id=%s FOR UPDATE",
                (registration_id, tid),
            )
            if not row or row["status"] != "pending" or tournament["status"] != "published":
                raise ValueError("Эту заявку уже нельзя изменить.")
            if approve:
                if approved_count(tid) >= tournament["max_participants"]:
                    raise ValueError("Достигнут лимит участников.")
                if row["team_id"]:
                    eligible_team(row["team_id"], tournament)
                status, reason = "approved", None
            else:
                reason = request.form.get("rejection_reason", "").strip()
                if not 5 <= len(reason) <= 255:
                    raise ValueError("Укажите причину отклонения (5–255 символов).")
                status = "rejected"
            db.execute(
                "UPDATE registrations SET status=%s,rejection_reason=%s WHERE id=%s",
                (status, reason, registration_id),
            )
        flash("Решение сохранено.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("organizer.registrations", tournament_id=tid))


@bp.post("/registrations/<int:registration_id>/approve")
@role_required("organizer")
def approve(registration_id):
    return moderate(registration_id, True)


@bp.post("/registrations/<int:registration_id>/reject")
@role_required("organizer")
def reject(registration_id):
    return moderate(registration_id, False)
