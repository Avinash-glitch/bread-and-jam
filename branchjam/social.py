import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from .db import get_db
from .utils import current_user, insert_notification, login_required, now_iso, popularity_score


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
    role = user["role"] or "creator"

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

    # Role-specific data
    creator_stats = {}
    producer_stats = {}
    collab_requests_for_me = []
    my_transactions = []
    following_list = []

    if role == "creator":
        samples_released = db.execute(
            "SELECT COUNT(*) AS cnt FROM projects WHERE owner_id = ?", (user["id"],)
        ).fetchone()["cnt"]

        collabs_given = db.execute(
            """
            SELECT COUNT(DISTINCT b.project_id) AS cnt
            FROM versions v
            JOIN branches b ON b.id = v.branch_id
            WHERE v.uploaded_by_user_id = ? AND b.project_id NOT IN (
                SELECT id FROM projects WHERE owner_id = ?
            )
            """,
            (user["id"], user["id"]),
        ).fetchone()["cnt"]

        money_row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE payee_id = ?",
            (user["id"],),
        ).fetchone()
        money_earned = money_row["total"] if money_row else 0.0

        follower_count = (user["followers_count"] or 0) if user["followers_count"] is not None else 0

        creator_stats = {
            "samples_released": samples_released,
            "collabs_given": collabs_given,
            "money_earned": money_earned,
            "follower_count": follower_count,
        }

        # Pending collab requests for this creator's projects
        collab_requests_for_me = db.execute(
            """
            SELECT cr.*, u.username AS requester_name, p.title AS project_title
            FROM collab_requests cr
            JOIN users u ON u.id = cr.requester_id
            JOIN projects p ON p.id = cr.project_id
            WHERE p.owner_id = ? AND cr.status = 'pending'
            ORDER BY cr.created_at DESC
            """,
            (user["id"],),
        ).fetchall()

        my_transactions = db.execute(
            """
            SELECT t.*, u.username AS payer_name
            FROM transactions t
            JOIN users u ON u.id = t.payer_id
            WHERE t.payee_id = ?
            ORDER BY t.created_at DESC
            LIMIT 20
            """,
            (user["id"],),
        ).fetchall()

    else:
        samples_bought = db.execute(
            """
            SELECT COUNT(*) AS cnt FROM transactions WHERE payer_id = ?
            """,
            (user["id"],),
        ).fetchone()["cnt"]

        money_spent_row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE payer_id = ?",
            (user["id"],),
        ).fetchone()
        money_spent = money_spent_row["total"] if money_spent_row else 0.0

        following_list = db.execute(
            """
            SELECT u.id, u.username, u.profile_picture
            FROM follows f
            JOIN users u ON u.id = f.followee_id
            WHERE f.follower_id = ?
            ORDER BY u.username
            """,
            (user["id"],),
        ).fetchall()

        producer_stats = {
            "samples_bought": samples_bought,
            "money_spent": money_spent,
            "following_count": len(following_list),
        }

        my_transactions = db.execute(
            """
            SELECT t.*, u.username AS payee_name
            FROM transactions t
            JOIN users u ON u.id = t.payee_id
            WHERE t.payer_id = ?
            ORDER BY t.created_at DESC
            LIMIT 20
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
        creator_stats=creator_stats,
        producer_stats=producer_stats,
        collab_requests_for_me=collab_requests_for_me,
        my_transactions=my_transactions,
        following_list=following_list,
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


@bp.route("/feed")
@login_required
def project_feed():
    db = get_db()
    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat(timespec="seconds")

    rows = db.execute(
        """
        SELECT
          p.*,
          u.username AS owner_name,
          u.id AS owner_id,
          COALESCE(p.listens, 0) AS listens,
          COALESCE(p.asking_price, 0) AS asking_price,
          COALESCE(p.collab_open, 0) AS collab_open,
          (
            SELECT COUNT(*) FROM transactions t
            JOIN offers o ON o.id = t.offer_id
            JOIN posts po ON po.id = o.post_id
            WHERE po.project_id = p.id
          ) AS purchases,
          (
            SELECT v2.file_path
            FROM versions v2
            JOIN branches b2 ON b2.id = v2.branch_id
            WHERE b2.project_id = p.id AND v2.file_path IS NOT NULL
            ORDER BY v2.created_at DESC
            LIMIT 1
          ) AS latest_file,
          (
            SELECT v3.waveform_svg
            FROM versions v3
            JOIN branches b3 ON b3.id = v3.branch_id
            WHERE b3.project_id = p.id AND v3.waveform_svg IS NOT NULL
            ORDER BY v3.created_at DESC
            LIMIT 1
          ) AS latest_waveform
        FROM projects p
        JOIN users u ON u.id = p.owner_id
        WHERE p.created_at >= ?
        ORDER BY p.created_at DESC
        LIMIT 60
        """,
        (cutoff,),
    ).fetchall()

    projects_with_score = sorted(
        rows,
        key=lambda r: popularity_score(r["listens"], r["purchases"]),
        reverse=True,
    )

    return render_template(
        "feed.html",
        title="Discover",
        projects=projects_with_score,
    )


# ── Follow / Unfollow ─────────────────────────────────────────────────────────

@bp.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    if user_id == session["user_id"]:
        return jsonify({"ok": False, "error": "Cannot follow yourself"}), 400
    db = get_db()
    try:
        db.execute(
            "INSERT INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?)",
            (session["user_id"], user_id, now_iso()),
        )
        db.execute(
            "UPDATE users SET followers_count = COALESCE(followers_count, 0) + 1 WHERE id = ?",
            (user_id,),
        )
        db.commit()
        insert_notification(db, user_id, "follow", {"follower_id": session["user_id"]})
        db.commit()
    except sqlite3.IntegrityError:
        pass  # already following
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("social.dashboard"))


@bp.route("/unfollow/<int:user_id>", methods=["POST"])
@login_required
def unfollow_user(user_id):
    db = get_db()
    db.execute(
        "DELETE FROM follows WHERE follower_id = ? AND followee_id = ?",
        (session["user_id"], user_id),
    )
    db.execute(
        "UPDATE users SET followers_count = MAX(0, COALESCE(followers_count, 0) - 1) WHERE id = ?",
        (user_id,),
    )
    db.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("social.dashboard"))


@bp.route("/followers")
@login_required
def followers_page():
    db = get_db()
    followers = db.execute(
        """
        SELECT u.id, u.username, u.profile_picture
        FROM follows f
        JOIN users u ON u.id = f.follower_id
        WHERE f.followee_id = ?
        ORDER BY f.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("followers.html", title="My Followers", followers=followers)


@bp.route("/following")
@login_required
def following_page():
    db = get_db()
    following = db.execute(
        """
        SELECT u.id, u.username, u.profile_picture
        FROM follows f
        JOIN users u ON u.id = f.followee_id
        WHERE f.follower_id = ?
        ORDER BY f.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("following.html", title="Following", following=following)


# ── Notifications ─────────────────────────────────────────────────────────────

@bp.route("/notifications")
@login_required
def notifications_json():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, type, payload, read, created_at
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (session["user_id"],),
    ).fetchall()
    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "type": row["type"],
            "payload": row["payload"],
            "read": bool(row["read"]),
            "created_at": row["created_at"],
        })
    return jsonify(result)


@bp.route("/notifications/all")
@login_required
def notifications_page():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, type, payload, read, created_at
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 100
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("notifications.html", title="Notifications", notifications=rows)


@bp.route("/notifications/<int:notif_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notif_id):
    db = get_db()
    db.execute(
        "UPDATE notifications SET read = 1 WHERE id = ? AND user_id = ?",
        (notif_id, session["user_id"]),
    )
    db.commit()
    return jsonify({"ok": True})
