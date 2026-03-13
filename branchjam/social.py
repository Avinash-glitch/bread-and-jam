from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .db import get_db
from .utils import current_user, login_required, now_iso


bp = Blueprint("social", __name__)


@bp.route("/")
def root():
    if session.get("user_id"):
        return redirect(url_for("social.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    db = get_db()

    incoming = db.execute(
        """
        SELECT fr.id, u.username AS sender
        FROM friend_requests fr
        JOIN users u ON u.id = fr.sender_id
        WHERE fr.receiver_id = ? AND fr.status = 'pending'
        ORDER BY fr.created_at DESC
        """,
        (user["id"],),
    ).fetchall()

    friends = db.execute(
        """
        SELECT u.id, u.username
        FROM friend_requests fr
        JOIN users u
          ON (u.id = fr.sender_id AND fr.receiver_id = ?)
          OR (u.id = fr.receiver_id AND fr.sender_id = ?)
        WHERE fr.status = 'accepted'
        ORDER BY u.username
        """,
        (user["id"], user["id"]),
    ).fetchall()

    friend_ids = [row["id"] for row in friends]
    recent_feed = []
    if friend_ids:
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat(timespec="seconds")
        placeholders = ",".join("?" for _ in friend_ids)
        recent_feed = db.execute(
            f"""
            SELECT
              v.id,
              v.version_number,
              v.notes,
              v.file_path,
              v.created_at,
              u.username AS uploader_name,
              p.title AS project_title
            FROM versions v
            JOIN branches b ON b.id = v.branch_id
            JOIN projects p ON p.id = b.project_id
            LEFT JOIN users u ON u.id = v.uploaded_by_user_id
            WHERE v.uploaded_by_user_id IN ({placeholders})
              AND v.created_at >= ?
            ORDER BY v.created_at DESC
            LIMIT 30
            """,
            (*friend_ids, cutoff),
        ).fetchall()

    projects = db.execute(
        """
        SELECT p.*, u.username AS owner_name
        FROM projects p
        JOIN users u ON u.id = p.owner_id
        ORDER BY p.created_at DESC
        """
    ).fetchall()

    pending_consents = db.execute(
        """
        SELECT
          dr.id AS request_id,
          dr.version_id,
          dr.created_at,
          u.username AS requester_name,
          p.title AS project_title
        FROM download_request_consents drc
        JOIN download_requests dr ON dr.id = drc.request_id
        JOIN users u ON u.id = dr.requester_id
        JOIN projects p ON p.id = dr.project_id
        WHERE drc.contributor_id = ? AND drc.decision = 'pending' AND dr.status = 'pending'
        ORDER BY dr.created_at DESC
        """,
        (user["id"],),
    ).fetchall()

    my_download_requests = db.execute(
        """
        SELECT
          dr.id AS request_id,
          dr.version_id,
          dr.status,
          dr.created_at,
          p.title AS project_title
        FROM download_requests dr
        JOIN projects p ON p.id = dr.project_id
        WHERE dr.requester_id = ?
        ORDER BY dr.created_at DESC
        """,
        (user["id"],),
    ).fetchall()

    return render_template(
        "dashboard.html",
        title="Dashboard",
        user=user,
        incoming=incoming,
        friends=friends,
        projects=projects,
        recent_feed=recent_feed,
        pending_consents=pending_consents,
        my_download_requests=my_download_requests,
    )


@bp.route("/friend-request", methods=["POST"])
@login_required
def send_friend_request():
    username = request.form.get("username", "").strip()
    user = current_user()
    db = get_db()
    target = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()

    if not target:
        flash("User not found.")
        return redirect(url_for("social.dashboard"))
    if target["id"] == user["id"]:
        flash("Cannot add yourself.")
        return redirect(url_for("social.dashboard"))

    sender, receiver = sorted([user["id"], target["id"]])
    existing = db.execute(
        "SELECT id, status FROM friend_requests WHERE sender_id = ? AND receiver_id = ?",
        (sender, receiver),
    ).fetchone()

    if existing:
        flash(f"Request already exists ({existing['status']}).")
        return redirect(url_for("social.dashboard"))

    db.execute(
        """
        INSERT INTO friend_requests (sender_id, receiver_id, status, created_at)
        VALUES (?, ?, 'pending', ?)
        """,
        (user["id"], target["id"], now_iso()),
    )
    db.commit()
    flash("Friend request sent.")
    return redirect(url_for("social.dashboard"))


@bp.route("/friend-request/<int:request_id>/respond", methods=["POST"])
@login_required
def respond_friend_request(request_id):
    action = request.form.get("action")
    if action not in {"accepted", "rejected"}:
        flash("Invalid action.")
        return redirect(url_for("social.dashboard"))
    db = get_db()
    req = db.execute("SELECT * FROM friend_requests WHERE id = ?", (request_id,)).fetchone()
    if not req or req["receiver_id"] != session["user_id"]:
        flash("Request not found.")
        return redirect(url_for("social.dashboard"))
    db.execute("UPDATE friend_requests SET status = ? WHERE id = ?", (action, request_id))
    db.commit()
    flash(f"Request {action}.")
    return redirect(url_for("social.dashboard"))


@bp.route("/rules-chat")
@login_required
def rules_chat():
    db = get_db()
    messages = db.execute(
        """
        SELECT rcm.*, u.username
        FROM rules_chat_messages rcm
        JOIN users u ON u.id = rcm.user_id
        ORDER BY rcm.created_at ASC
        """
    ).fetchall()
    return render_template("rules_chat.html", title="Rules Chat", messages=messages)


@bp.route("/rules-chat", methods=["POST"])
@login_required
def post_rules_chat_message():
    message = request.form.get("message", "").strip()
    if not message:
        flash("Message cannot be empty.")
        return redirect(url_for("social.rules_chat"))
    if len(message) > 1000:
        flash("Message is too long (max 1000 chars).")
        return redirect(url_for("social.rules_chat"))

    db = get_db()
    db.execute(
        "INSERT INTO rules_chat_messages (user_id, message, created_at) VALUES (?, ?, ?)",
        (session["user_id"], message, now_iso()),
    )
    db.commit()
    flash("Posted to rules chat.")
    return redirect(url_for("social.rules_chat"))


@bp.route("/feed/<int:version_id>/<action>", methods=["POST"])
@login_required
def feed_action(version_id, action):
    action = (action or "").strip().lower()
    if action not in {"like", "forward", "share", "collaborate", "bid"}:
        flash("Unknown action.")
        return redirect(url_for("social.dashboard"))
    db = get_db()
    version = db.execute("SELECT id FROM versions WHERE id = ?", (version_id,)).fetchone()
    if not version:
        flash("Post not found.")
        return redirect(url_for("social.dashboard"))
    flash(f"{action.title()} recorded for version #{version_id}.")
    return redirect(url_for("social.dashboard"))
