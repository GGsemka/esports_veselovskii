from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from pymysql.err import IntegrityError, OperationalError
from werkzeug.security import generate_password_hash

from . import db
from .auth import identity_errors, read_identity_form
from .security import role_required

bp = Blueprint("admin", __name__)
ROLES = ("player", "organizer", "admin")


@bp.get("/admin/users")
@role_required("admin")
def users():
    rows = db.all("SELECT id,nickname,email,role,created_at FROM users ORDER BY nickname,id")
    return render_template("admin_users.html", users=rows, roles=ROLES)


@bp.post("/admin/users")
@role_required("admin")
def create_user():
    form = read_identity_form()
    role = request.form.get("role", "")
    password = request.form.get("password", "")
    errors = identity_errors(form, password, password, password_required=True)
    if role not in ROLES:
        errors.append("Выберите роль пользователя.")
    if not errors:
        try:
            db.execute(
                "INSERT INTO users (nickname,email,password_hash,role) VALUES (%s,%s,%s,%s)",
                (form["nickname"], form["email"], generate_password_hash(password), role),
            )
        except IntegrityError as error:
            if error.args[0] != 1062:
                raise
            errors = ["Никнейм или адрес электронной почты уже занят."]
        else:
            flash("Пользователь создан.", "success")
    for message in errors:
        flash(message, "error")
    return redirect(url_for("admin.users"))


def lock_administrators():
    admins = db.all("SELECT id FROM users WHERE role='admin' ORDER BY id FOR UPDATE")
    ids = {admin["id"] for admin in admins}
    if g.user["id"] not in ids:
        abort(403)
    return ids


@bp.post("/admin/users/<int:user_id>")
@role_required("admin")
def update_user(user_id):
    try:
        with db.transaction():
            admins = lock_administrators()
            user = db.one("SELECT nickname,email,role FROM users WHERE id=%s FOR UPDATE", (user_id,))
            if not user:
                abort(404)
            form = read_identity_form(user)
            role = request.form.get("role", user["role"])
            password = request.form.get("password", "")
            errors = identity_errors(form, password, password, user_id=user_id)
            if role not in ROLES:
                errors.append("Выберите роль пользователя.")
            if role != "admin" and admins == {user_id}:
                errors.append("Нельзя изменить роль последнего администратора.")
            if not errors:
                db.execute(
                    """UPDATE users SET nickname=%s,email=%s,role=%s,
                              password_hash=COALESCE(%s,password_hash) WHERE id=%s""",
                    (
                        form["nickname"],
                        form["email"],
                        role,
                        generate_password_hash(password) if password else None,
                        user_id,
                    ),
                )
    except IntegrityError as error:
        if error.args[0] != 1062:
            raise
        errors = ["Никнейм или адрес электронной почты уже занят."]
    if not errors:
        flash("Пользователь обновлён.", "success")
    for message in errors:
        flash(message, "error")
    return redirect(url_for("admin.users"))


@bp.post("/admin/users/<int:user_id>/delete")
@role_required("admin")
def delete_user(user_id):
    try:
        with db.transaction():
            admins = lock_administrators()
            if not db.one("SELECT id FROM users WHERE id=%s FOR UPDATE", (user_id,)):
                abort(404)
            if user_id == g.user["id"] or admins == {user_id}:
                raise ValueError("Нельзя удалить свой аккаунт или последнего администратора.")
            team = db.one(
                """SELECT t.id FROM team_members m JOIN teams t ON t.id=m.team_id
                             WHERE m.user_id=%s FOR UPDATE OF t""",
                (user_id,),
            )
            if db.one("SELECT id FROM tournaments WHERE organizer_id=%s LIMIT 1 FOR UPDATE", (user_id,)):
                raise ValueError("Нельзя удалить организатора, у которого есть турниры.")
            registration_owner = (user_id, team["id"] if team else None)
            # Bracket generation locks its tournament before reading approved registrations.
            db.all(
                """SELECT t.id FROM registrations r JOIN tournaments t ON t.id=r.tournament_id
                      WHERE r.user_id=%s OR r.team_id=%s ORDER BY t.id FOR UPDATE OF t""",
                registration_owner,
            )
            if db.one(
                """SELECT r.id FROM registrations r JOIN tournaments t ON t.id=r.tournament_id
                         JOIN matches m ON m.tournament_id=t.id AND r.id IN (m.registration1_id,m.registration2_id)
                         WHERE (r.user_id=%s OR r.team_id=%s) AND r.status='approved'
                         AND t.status='in_progress' LIMIT 1 FOR UPDATE""",
                registration_owner,
            ):
                raise ValueError("Нельзя удалить участника сетки, пока турнир идёт.")
            db.execute("DELETE FROM users WHERE id=%s", (user_id,))
    except ValueError as error:
        flash(str(error), "error")
    except IntegrityError as error:
        if error.args[0] != 1451:
            raise
        flash("Нельзя удалить организатора, у которого есть турниры.", "error")
    except OperationalError as error:
        if error.args[0] not in {1205, 1213}:
            raise
        flash("Данные пользователя меняются. Повторите удаление позже.", "error")
    else:
        flash("Пользователь удалён.", "success")
    return redirect(url_for("admin.users"))


@bp.get("/admin/games")
@role_required("admin")
def games():
    return render_template("admin_games.html", games=db.all("SELECT * FROM games ORDER BY name,id"))


def save_game(game_id=None):
    name = request.form.get("name", "").strip()
    game_format = request.form.get("format", "")
    minimum = request.form.get("team_size_min", type=int)
    maximum = request.form.get("team_size_max", type=int)
    try:
        if not 1 <= len(name) <= 120:
            raise ValueError("Введите название игры длиной до 120 символов.")
        if game_format not in {"team", "solo"}:
            raise ValueError("Выберите формат игры.")
        if minimum is None or maximum is None or not 1 <= minimum <= maximum <= 2147483647:
            raise ValueError(
                "Укажите положительные размеры команды. Минимум не должен превышать максимум."
            )
        if game_format == "solo" and (minimum, maximum) != (1, 1):
            raise ValueError("Для одиночного формата оба размера должны быть равны 1.")
        fields = (name, minimum, maximum, game_format)
        with db.transaction():
            if game_id is None:
                db.execute(
                    "INSERT INTO games (name,team_size_min,team_size_max,format) VALUES (%s,%s,%s,%s)",
                    fields,
                )
            else:
                if not db.one("SELECT id FROM games WHERE id=%s FOR UPDATE", (game_id,)):
                    abort(404)
                db.execute(
                    "UPDATE games SET name=%s,team_size_min=%s,team_size_max=%s,format=%s WHERE id=%s",
                    (*fields, game_id),
                )
    except ValueError as error:
        flash(str(error), "error")
    except IntegrityError as error:
        if error.args[0] != 1062:
            raise
        flash("Игра с таким названием уже существует.", "error")
    else:
        flash("Игра сохранена.", "success")
    return redirect(url_for("admin.games"))


@bp.post("/admin/games")
@role_required("admin")
def create_game():
    return save_game()


@bp.post("/admin/games/<int:game_id>")
@role_required("admin")
def update_game(game_id):
    return save_game(game_id)


@bp.post("/admin/games/<int:game_id>/delete")
@role_required("admin")
def delete_game(game_id):
    in_use = "Нельзя удалить игру, пока она связана с командами или турнирами."
    try:
        with db.transaction():
            if not db.one("SELECT id FROM games WHERE id=%s FOR UPDATE", (game_id,)):
                abort(404)
            if db.one("SELECT id FROM teams WHERE game_id=%s LIMIT 1", (game_id,)) or db.one(
                "SELECT id FROM tournaments WHERE game_id=%s LIMIT 1", (game_id,)
            ):
                raise ValueError(in_use)
            db.execute("DELETE FROM games WHERE id=%s", (game_id,))
    except ValueError as error:
        flash(str(error), "error")
    except IntegrityError as error:
        if error.args[0] != 1451:
            raise
        flash(in_use, "error")
    else:
        flash("Игра удалена.", "success")
    return redirect(url_for("admin.games"))
