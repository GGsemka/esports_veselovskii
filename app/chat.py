from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import db
from .security import role_required

bp = Blueprint("chat", __name__)
THREADS = """SELECT ct.id,ct.tournament_id,ct.participant_user_id,ct.created_at,t.title,t.status,
                    ga.name AS game_name,t.organizer_id,
                    CASE WHEN t.registration_type='team' THEN COALESCE(tm.name,u.nickname)
                         ELSE u.nickname END AS participant_name
             FROM chat_threads ct JOIN tournaments t ON t.id=ct.tournament_id
             JOIN games ga ON ga.id=t.game_id
             JOIN users u ON u.id=ct.participant_user_id
             LEFT JOIN teams tm ON tm.captain_id=u.id"""


def get_thread(thread_id):
    thread = db.one(THREADS + " WHERE ct.id=%s", (thread_id,))
    if not thread:
        abort(404)
    role = g.user["role"]
    user_id = g.user["id"]
    if (
        role == "organizer"
        and thread["organizer_id"] == user_id
        or role == "player"
        and thread["participant_user_id"] == user_id
    ):
        return thread
    abort(403)


def read_messages(thread_id, after=0):
    return db.all(
        """SELECT m.id,m.sender_id,u.nickname,m.text,m.created_at
                     FROM chat_messages m JOIN users u ON u.id=m.sender_id
                     WHERE m.thread_id=%s AND m.id>%s ORDER BY m.id""",
        (thread_id, after),
    )


@bp.get("/tournaments/<int:tournament_id>/chats")
@role_required("organizer")
def threads(tournament_id):
    tournament = db.one("SELECT id,title,organizer_id FROM tournaments WHERE id=%s", (tournament_id,))
    if not tournament:
        abort(404)
    if tournament["organizer_id"] != g.user["id"]:
        abort(403)
    rows = db.all(
        """SELECT inbox.*,m.text AS last_text,m.created_at AS last_at
                     FROM ("""
        + THREADS
        + """ WHERE ct.tournament_id=%s) inbox
                     LEFT JOIN chat_messages m ON m.id=(
                         SELECT latest.id FROM chat_messages latest WHERE latest.thread_id=inbox.id
                         ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1
                     )
                     ORDER BY m.created_at DESC,m.id DESC,inbox.created_at DESC,inbox.id DESC""",
        (tournament_id,),
    )
    return render_template("chat_threads.html", tournament=tournament, threads=rows)


@bp.route("/chats/<int:thread_id>", methods=["GET", "POST"])
@role_required("player", "organizer")
def thread(thread_id):
    current_thread = get_thread(thread_id)
    if request.method == "POST":
        message = request.form.get("text", "").strip()
        if not 1 <= len(message) <= 2000:
            flash("Введите сообщение длиной до 2000 символов.", "error")
        else:
            with db.transaction():
                if not db.one("SELECT id FROM chat_threads WHERE id=%s FOR UPDATE", (thread_id,)):
                    abort(404)
                db.execute(
                    "INSERT INTO chat_messages (thread_id,sender_id,text) VALUES (%s,%s,%s)",
                    (thread_id, g.user["id"], message),
                )
        return redirect(url_for("chat.thread", thread_id=thread_id))
    return render_template("chat.html", thread=current_thread, messages=read_messages(thread_id))


@bp.get("/chats/<int:thread_id>/messages")
@role_required("player", "organizer")
def messages(thread_id):
    get_thread(thread_id)
    after = min(2147483647, max(0, request.args.get("after", 0, type=int)))
    rows = read_messages(thread_id, after)
    for row in rows:
        row["created_at"] = row["created_at"].strftime("%d.%m.%Y %H:%M")
    response = jsonify(messages=rows)
    response.cache_control.no_store = True
    return response
