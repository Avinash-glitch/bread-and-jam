import json
import os
import uuid
from datetime import datetime
from functools import wraps

from flask import abort, current_app, redirect, session, url_for
from werkzeug.utils import secure_filename

from .db import get_db


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)

    return wrapper


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def allowed_file(filename):
    allowed_extensions = current_app.config["ALLOWED_EXTENSIONS"]
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def save_upload(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        return None
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    safe_name = secure_filename(file_storage.filename.rsplit(".", 1)[0])
    file_name = f"{safe_name}_{uuid.uuid4().hex[:10]}.{ext}"
    path = os.path.join(current_app.config["UPLOAD_DIR"], file_name)
    file_storage.save(path)
    return file_name


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def role_required(*roles):
    """Decorator that aborts 403 if the session role is not in the allowed set."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if session.get("role") not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def popularity_score(listens, purchases):
    """Simple popularity ranking: listens count for 1pt, purchases for 5pt."""
    return (listens or 0) * 1.0 + (purchases or 0) * 5.0


def save_avatar(file_storage, user_id):
    """Save an avatar image to uploads/avatars/<user_id>_<ts>.<ext>.

    Allowed extensions: jpg, jpeg, png, webp.
    Returns a relative path like 'avatars/filename.jpg', or None on failure.
    """
    if not file_storage or not file_storage.filename:
        return None
    allowed = {"jpg", "jpeg", "png", "webp"}
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in allowed:
        return None
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id}_{ts}.{ext}"
    avatar_dir = os.path.join(current_app.config["UPLOAD_DIR"], "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    file_storage.save(os.path.join(avatar_dir, filename))
    return f"avatars/{filename}"


def insert_notification(db, user_id, notif_type, payload_dict):
    """Insert a notification row and emit a SocketIO event to the user's room."""
    payload_json = json.dumps(payload_dict) if payload_dict else None
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO notifications (user_id, type, payload, read, created_at) VALUES (?, ?, ?, 0, ?)",
        (user_id, notif_type, payload_json, created_at),
    )
    # Import socketio inside function body to avoid circular imports.
    try:
        from branchjam import socketio  # noqa: PLC0415
        socketio.emit(
            "notification",
            {"type": notif_type, "payload": payload_dict or {}},
            room=f"user_{user_id}",
        )
    except Exception:
        pass

