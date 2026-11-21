import hmac
import secrets
from functools import wraps

from flask import abort, g, redirect, request, session, url_for

from . import db


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def load_identity():
    g.user = None
    if request.endpoint == "static":
        return
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        actual = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(expected.encode(), actual.encode()):
            abort(403, "Форма устарела. Обновите страницу и попробуйте снова.")
    if session.get("user_id"):
        g.user = db.one("SELECT id,nickname,email,role FROM users WHERE id=%s", (session["user_id"],))
        if not g.user:
            session.clear()


def role_required(*roles):
    def decorate(view):
        @wraps(view)
        def checked(*args, **kwargs):
            if not g.user:
                return redirect(url_for("auth.login"))
            if roles and g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return checked

    return decorate
