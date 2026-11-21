import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, has_request_context, render_template, send_from_directory, url_for
from werkzeug.exceptions import HTTPException

from . import db
from .config import configuration
from .security import csrf_token, load_identity


class RequestLog(logging.Filter):
    def filter(self, record):
        user = getattr(g, "user", None) if has_request_context() else None
        record.user_id = user["id"] if user else "-"
        return True


def create_app(config_name=None, config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    if isinstance(config_name, dict):
        config, config_name = config_name, "test"
    app.config.update(configuration(config_name or os.getenv("APP_ENV", "dev"), app.instance_path))
    app.config.update(config or {})
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    from . import admin, auth, cabinet, chat, cli, competition, organizer, public, teams

    for module in (public, organizer, competition, auth, teams, cabinet, chat, admin):
        app.register_blueprint(module.bp)
    cli.register(app)
    app.before_request(load_identity)
    app.teardown_appcontext(db.close)
    app.jinja_env.globals.update(csrf_token=csrf_token, media_url=media_url)

    @app.get("/uploads/<filename>")
    def media(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, max_age=86400)

    @app.errorhandler(HTTPException)
    def error_page(error):
        messages = {
            400: "Проверьте отправленные данные.",
            403: "Для этого действия нужны другие права.",
            404: "Страница недоступна или была удалена.",
            405: "Метод запроса недоступен.",
            409: "Действие недоступно в текущем состоянии.",
            413: "Размер запроса превышает 4 МБ.",
            500: "Попробуйте повторить запрос позже.",
        }
        message = messages.get(error.code, "Не удалось выполнить запрос.")
        if error.code != 500 and error.description != type(error).description:
            message = error.description
        response = error.get_response()
        response.set_data(render_template("error.html", code=error.code, message=message))
        response.content_type = "text/html; charset=utf-8"
        return response

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if response.status_code >= 500:
            app.logger.error("request failed status=%s", response.status_code)
        return response

    app.logger.setLevel(logging.INFO)
    for handler in app.logger.handlers:
        handler.addFilter(RequestLog())
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s user_id=%(user_id)s %(message)s")
        )
    return app


def media_url(name):
    if name.startswith("art/"):
        return url_for("static", filename=name)
    return url_for("media", filename=name)
