import os

from flask import Flask

from . import auth, projects, social
from .db import close_db, init_db


def create_app():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_dir = os.path.join(base_dir, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = "dev-secret-change-me"
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB
    app.config["DB_PATH"] = os.path.join(base_dir, "branchjam.db")
    app.config["UPLOAD_DIR"] = upload_dir
    app.config["ALLOWED_EXTENSIONS"] = {
        "wav",
        "mp3",
        "m4a",
        "aac",
        "flac",
        "ogg",
        "opus",
        "aiff",
        "aif",
    }
    app.config["TEMPO_TOLERANCE_BPM"] = 3.0

    app.teardown_appcontext(close_db)
    app.register_blueprint(auth.bp)
    app.register_blueprint(social.bp)
    app.register_blueprint(projects.bp)

    with app.app_context():
        init_db()

    return app
