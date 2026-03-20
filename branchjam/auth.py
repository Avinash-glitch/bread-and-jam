import json
import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .utils import login_required, now_iso, save_avatar


bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "creator").strip().lower()
        if role not in {"creator", "producer"}:
            role = "creator"
        if len(username) < 3 or len(password) < 4:
            flash("Username must be 3+ chars and password 4+ chars.")
            return redirect(url_for("auth.register"))

        bio = request.form.get("bio", "").strip() or None
        genres_raw = request.form.get("genres", "").strip()
        instruments_raw = request.form.get("instruments", "").strip()
        genres_json = json.dumps([g.strip() for g in genres_raw.split(",") if g.strip()]) if genres_raw else None
        instruments_json = json.dumps([i.strip() for i in instruments_raw.split(",") if i.strip()]) if instruments_raw else None

        db = get_db()
        try:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, generate_password_hash(password), role, now_iso()),
            )
            user_id = cur.lastrowid

            # Handle optional avatar upload
            avatar_path = save_avatar(request.files.get("profile_picture"), user_id)

            db.execute(
                "UPDATE users SET bio = ?, genres = ?, instruments = ?, profile_picture = ? WHERE id = ?",
                (bio, genres_json, instruments_json, avatar_path, user_id),
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
        role = user["role"] if user["role"] else "creator"
        if user["role"] is None:
            db = get_db()
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user["id"]))
            db.commit()
        session["user_id"] = user["id"]
        session["role"] = role
        return redirect(url_for("social.dashboard"))
    return render_template("login.html", title="Login")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/profile/<int:user_id>")
@login_required
def view_profile(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return "User not found", 404

    projects_count = db.execute(
        "SELECT COUNT(*) AS cnt FROM projects WHERE owner_id = ?", (user_id,)
    ).fetchone()["cnt"]

    collabs_count = db.execute(
        """
        SELECT COUNT(DISTINCT b.project_id) AS cnt
        FROM versions v
        JOIN branches b ON b.id = v.branch_id
        WHERE v.uploaded_by_user_id = ? AND b.project_id NOT IN (
            SELECT id FROM projects WHERE owner_id = ?
        )
        """,
        (user_id, user_id),
    ).fetchone()["cnt"]

    money_row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE payee_id = ?",
        (user_id,),
    ).fetchone()
    money_earned = money_row["total"] if money_row else 0.0

    projects = db.execute(
        "SELECT * FROM projects WHERE owner_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()

    return render_template(
        "profile.html",
        title=f"{user['username']}'s Profile",
        profile_user=user,
        projects_count=projects_count,
        collabs_count=collabs_count,
        money_earned=money_earned,
        projects=projects,
    )


@bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

    if request.method == "POST":
        bio = request.form.get("bio", "").strip() or None
        genres_raw = request.form.get("genres", "").strip()
        instruments_raw = request.form.get("instruments", "").strip()
        genres_json = json.dumps([g.strip() for g in genres_raw.split(",") if g.strip()]) if genres_raw else None
        instruments_json = json.dumps([i.strip() for i in instruments_raw.split(",") if i.strip()]) if instruments_raw else None

        avatar_path = save_avatar(request.files.get("profile_picture"), session["user_id"])
        if avatar_path is None:
            # Keep existing picture if no new one uploaded
            avatar_path = user["profile_picture"] if user else None

        db.execute(
            "UPDATE users SET bio = ?, genres = ?, instruments = ?, profile_picture = ? WHERE id = ?",
            (bio, genres_json, instruments_json, avatar_path, session["user_id"]),
        )
        db.commit()
        flash("Profile updated.")
        return redirect(url_for("auth.view_profile", user_id=session["user_id"]))

    return render_template("profile_edit.html", title="Edit Profile", user=user)
