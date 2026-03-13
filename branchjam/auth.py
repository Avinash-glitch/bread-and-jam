import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .utils import now_iso


bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 4:
            flash("Username must be 3+ chars and password 4+ chars.")
            return redirect(url_for("auth.register"))
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), now_iso()),
            )
            db.commit()
            flash("Account created. Please log in.")
            return redirect(url_for("auth.login"))
        except sqlite3.IntegrityError:
            flash("Username already exists.")
    return render_template("register.html", title="Register")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid credentials.")
            return redirect(url_for("auth.login"))
        session["user_id"] = user["id"]
        return redirect(url_for("social.dashboard"))
    return render_template("login.html", title="Login")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

