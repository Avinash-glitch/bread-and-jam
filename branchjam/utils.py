import os
import uuid
from datetime import datetime
from functools import wraps

from flask import current_app, redirect, session, url_for
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

