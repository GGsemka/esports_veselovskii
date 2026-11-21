import re

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from pymysql.err import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .security import role_required

bp = Blueprint("auth", __name__)
NICKNAME = re.compile(r"[A-Za-z0-9_]{3,20}")
EMAIL_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
EMAIL = re.compile(
    rf"{EMAIL_ATOM}(?:\.{EMAIL_ATOM})*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{{2,}}"
)
DUMMY_HASH = generate_password_hash("invalid account")


def read_identity_form(default=None):
    default = default or {}
    return {
        field: request.form.get(field, default.get(field, "")).strip() for field in ("nickname", "email")
    }


def identity_errors(form, password, confirm, *, user_id=None, password_required=False):
    errors = []
    nickname = form["nickname"]
    email = form["email"]
    exclude = user_id or 0
    if not NICKNAME.fullmatch(nickname):
        errors.append("Никнейм должен содержать 3–20 латинских букв, цифр или символов подчёркивания.")
    elif db.one("SELECT id FROM users WHERE nickname=%s AND id<>%s", (nickname, exclude)):
        errors.append("Этот никнейм уже занят.")
    if len(email) > 120 or not EMAIL.fullmatch(email):
        errors.append("Введите корректный адрес электронной почты.")
    elif db.one("SELECT id FROM users WHERE email=%s AND id<>%s", (email, exclude)):
        errors.append("Этот адрес электронной почты уже зарегистрирован.")
    if password_required or password or confirm:
        if not 8 <= len(password) <= 1024:
            errors.append("Пароль должен содержать от 8 до 1024 символов.")
        if password != confirm:
            errors.append("Пароли не совпадают.")
    return errors


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("public.index"))
    form = {}
    if request.method == "POST":
        form = read_identity_form()
        password = request.form.get("password", "")
        errors = identity_errors(form, password, request.form.get("confirm", ""), password_required=True)
        if not errors:
            try:
                db.execute(
                    "INSERT INTO users (nickname,email,password_hash,role) VALUES (%s,%s,%s,'player')",
                    (form["nickname"], form["email"], generate_password_hash(password)),
                )
            except IntegrityError as error:
                if error.args[0] != 1062:
                    raise
                errors = ["Никнейм или адрес электронной почты уже занят."]
            else:
                flash("Аккаунт создан. Войдите в систему.", "success")
                return redirect(url_for("auth.login"))
        for message in errors:
            flash(message, "error")
    return render_template("register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("public.index"))
    form = {}
    if request.method == "POST":
        login_value = request.form.get("login", "").strip()
        password = request.form.get("password", "")
        form["login"] = login_value
        user = (
            db.one(
                "SELECT id,password_hash FROM users WHERE email=%s OR nickname=%s",
                (login_value, login_value),
            )
            if len(login_value) <= 120
            else None
        )
        valid = len(password) <= 1024 and check_password_hash(
            user["password_hash"] if user else DUMMY_HASH, password
        )
        if user and valid:
            session.clear()
            session["user_id"] = user["id"]
            current_app.logger.info("login succeeded authenticated_user_id=%s", user["id"])
            return redirect(url_for("public.index"))
        current_app.logger.warning("login rejected")
        flash("Неверный логин или пароль", "error")
    return render_template("login.html", form=form)


@bp.post("/logout")
def logout():
    if g.user:
        current_app.logger.info("logout succeeded")
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("public.index"))


@bp.route("/profile", methods=["GET", "POST"])
@role_required("player")
def profile():
    form = {field: g.user[field] for field in ("nickname", "email")}
    if request.method == "POST":
        form = read_identity_form()
        password = request.form.get("password", "")
        errors = identity_errors(form, password, request.form.get("confirm", ""), user_id=g.user["id"])
        if not errors:
            try:
                db.execute(
                    """UPDATE users SET nickname=%s,email=%s,password_hash=COALESCE(%s,password_hash)
                              WHERE id=%s""",
                    (
                        form["nickname"],
                        form["email"],
                        generate_password_hash(password) if password else None,
                        g.user["id"],
                    ),
                )
            except IntegrityError as error:
                if error.args[0] != 1062:
                    raise
                errors = ["Никнейм или адрес электронной почты уже занят."]
            else:
                flash("Профиль обновлён.", "success")
                return redirect(url_for("auth.profile"))
        for message in errors:
            flash(message, "error")
    return render_template("profile.html", form=form)
