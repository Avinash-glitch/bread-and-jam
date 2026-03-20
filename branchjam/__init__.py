import json
import os

from flask import Flask
from flask_socketio import SocketIO

from . import auth, marketplace, projects, social
from .db import close_db, init_db

# SocketIO instance created here so jam.py can import it.
# We init it with the app later inside create_app().
socketio = SocketIO()


def create_app():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_dir = os.path.join(base_dir, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    app = Flask(__name__, template_folder="templates", static_folder=os.path.join(base_dir, "static"))
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

    # Register custom Jinja2 filters
    @app.template_filter("from_json")
    def from_json_filter(value):
        if not value:
            return []
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return []

    app.teardown_appcontext(close_db)
    app.register_blueprint(auth.bp)
    app.register_blueprint(social.bp)
    app.register_blueprint(projects.bp)
    app.register_blueprint(marketplace.bp)

    from . import jam  # import after socketio is defined
    app.register_blueprint(jam.bp)

    with app.app_context():
        init_db()

    # async_mode='threading' works with Flask's dev server out of the box.
    # For production you'd switch to eventlet or gevent.
    socketio.init_app(app, async_mode="threading", cors_allowed_origins="*")

    @socketio.on("authenticate")
    def on_authenticate(data):
        from flask_socketio import join_room
        user_id = data.get("user_id")
        if user_id:
            join_room(f"user_{user_id}")

    return app
