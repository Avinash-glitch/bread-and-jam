import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from .db import get_db
from .utils import current_user, insert_notification, login_required, now_iso


bp = Blueprint("marketplace", __name__)


def _project_contributors(db, project_id):
    rows = db.execute(
        """
        SELECT DISTINCT v.uploaded_by_user_id AS uid
        FROM versions v
        JOIN branches b ON b.id = v.branch_id
        WHERE b.project_id = ? AND v.uploaded_by_user_id IS NOT NULL
        """,
        (project_id,),
    ).fetchall()
    contributor_ids = {row["uid"] for row in rows}
    owner = db.execute("SELECT owner_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if owner:
        contributor_ids.add(owner["owner_id"])
    return contributor_ids


def _offer_status_from_responses(db, offer_id):
    rows = db.execute(
        "SELECT decision FROM offer_responses WHERE offer_id = ?",
        (offer_id,),
    ).fetchall()
    if not rows:
        return "pending"
    decisions = [row["decision"] for row in rows]
    total = len(decisions)
    accepted = sum(1 for d in decisions if d == "accepted")
    rejected = sum(1 for d in decisions if d == "rejected")
    countered = any(d == "countered" for d in decisions)

    if countered:
        return "countered"
    if rejected > total / 2:
        return "rejected"
    if accepted > total / 2:
        return "accepted"
    return "pending"


def _insert_splits_and_notify(db, offer_id, contributor_ids, total_amount):
    """Insert collaborator_splits rows and notify each contributor."""
    if not contributor_ids:
        return
    split = total_amount / len(contributor_ids)
    for cid in sorted(contributor_ids):
        db.execute(
            """
            INSERT INTO collaborator_splits (offer_id, contributor_id, split_amount, accepted, decided_at)
            VALUES (?, ?, ?, 0, NULL)
            """,
            (offer_id, cid, split),
        )
        insert_notification(db, cid, "new_offer", {
            "offer_id": offer_id,
            "split_amount": round(split, 2),
        })


@bp.route("/marketplace")
@login_required
def marketplace_index():
    user = current_user()
    db = get_db()
    posts = db.execute(
        """
        SELECT p.*, u.username AS creator_name, pr.title AS project_title
        FROM posts p
        JOIN users u ON u.id = p.creator_id
        JOIN projects pr ON pr.id = p.project_id
        WHERE p.status = 'open'
        ORDER BY p.created_at DESC
        """
    ).fetchall()
    return render_template(
        "marketplace.html",
        title="Marketplace",
        posts=posts,
        user_role=user["role"],
    )


@bp.route("/marketplace/post/new", methods=["GET", "POST"])
@login_required
def create_post():
    user = current_user()
    if user["role"] != "creator":
        flash("Only creators can create posts.")
        return redirect(url_for("marketplace.marketplace_index"))

    db = get_db()
    projects = db.execute(
        """
        SELECT DISTINCT p.*
        FROM projects p
        LEFT JOIN branches b ON b.project_id = p.id
        LEFT JOIN versions v ON v.branch_id = b.id
        WHERE p.owner_id = ? OR v.uploaded_by_user_id = ?
        ORDER BY p.created_at DESC
        """,
        (user["id"], user["id"]),
    ).fetchall()

    selected_project_id = request.form.get("project_id")
    versions = []
    if selected_project_id:
        versions = db.execute(
            """
            SELECT v.*, b.name AS branch_name
            FROM versions v
            JOIN branches b ON b.id = v.branch_id
            WHERE b.project_id = ?
            ORDER BY v.created_at DESC
            """,
            (selected_project_id,),
        ).fetchall()

    if request.method == "POST" and request.form.get("submit") == "create_post":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        project_id = request.form.get("project_id")
        track_ids = request.form.getlist("track_id")
        if not title or not project_id or not track_ids:
            flash("Title, project, and at least one track are required.")
            return redirect(url_for("marketplace.create_post"))

        cur = db.execute(
            """
            INSERT INTO posts (project_id, creator_id, title, description, status, created_at)
            VALUES (?, ?, ?, ?, 'open', ?)
            """,
            (project_id, user["id"], title, description, now_iso()),
        )
        post_id = cur.lastrowid

        for tid in track_ids:
            price_raw = request.form.get(f"price_{tid}", "0")
            try:
                price = max(0.0, float(price_raw))
            except ValueError:
                price = 0.0
            db.execute(
                """
                INSERT INTO post_tracks (post_id, version_id, price, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (post_id, int(tid), price, now_iso()),
            )

        db.commit()
        flash("Post created and accepting offers.")
        return redirect(url_for("marketplace.view_post", post_id=post_id))

    return render_template(
        "create_post.html",
        title="Create Post",
        projects=projects,
        versions=versions,
        selected_project_id=selected_project_id,
    )


@bp.route("/marketplace/post/<int:post_id>")
@login_required
def view_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute(
        """
        SELECT p.*, u.username AS creator_name, pr.title AS project_title
        FROM posts p
        JOIN users u ON u.id = p.creator_id
        JOIN projects pr ON pr.id = p.project_id
        WHERE p.id = ?
        """,
        (post_id,),
    ).fetchone()
    if not post:
        return "Post not found", 404

    tracks = db.execute(
        """
        SELECT pt.*, v.notes, v.file_path, b.name AS branch_name
        FROM post_tracks pt
        JOIN versions v ON v.id = pt.version_id
        JOIN branches b ON b.id = v.branch_id
        WHERE pt.post_id = ?
        """,
        (post_id,),
    ).fetchall()

    offers = db.execute(
        """
        SELECT o.*, u.username AS producer_name
        FROM offers o
        JOIN users u ON u.id = o.producer_id
        WHERE o.post_id = ?
        ORDER BY o.total_amount DESC
        """,
        (post_id,),
    ).fetchall()

    responses = {}
    if offers:
        rows = db.execute(
            """
            SELECT * FROM offer_responses WHERE offer_id IN (
                SELECT id FROM offers WHERE post_id = ?
            )
            """,
            (post_id,),
        ).fetchall()
        for row in rows:
            responses.setdefault(row["offer_id"], []).append(row)

    return render_template(
        "post_detail.html",
        title=post["title"],
        post=post,
        tracks=tracks,
        offers=offers,
        responses=responses,
        user_role=user["role"],
    )


@bp.route("/marketplace/post/<int:post_id>/offer", methods=["POST"])
@login_required
def make_offer(post_id):
    user = current_user()
    if user["role"] != "producer":
        flash("Only producers can make offers.")
        return redirect(url_for("marketplace.view_post", post_id=post_id))

    track_ids = request.form.getlist("track_id")
    total_raw = request.form.get("total_amount", "").strip()
    hours_raw = request.form.get("expires_hours", "24").strip()
    try:
        total_amount = float(total_raw)
    except ValueError:
        total_amount = 0.0
    try:
        hours = int(hours_raw)
    except ValueError:
        hours = 24
    hours = max(1, min(168, hours))

    if total_amount <= 0 or not track_ids:
        flash("Select tracks and enter a valid offer amount.")
        return redirect(url_for("marketplace.view_post", post_id=post_id))

    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post or post["status"] != "open":
        flash("Post is not accepting offers.")
        return redirect(url_for("marketplace.view_post", post_id=post_id))

    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(hours=hours)).isoformat(timespec="seconds")

    cur = db.execute(
        """
        INSERT INTO offers (post_id, producer_id, total_amount, status, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        """,
        (post_id, user["id"], total_amount, expires_at, now_iso(), now_iso()),
    )
    offer_id = cur.lastrowid

    for tid in track_ids:
        db.execute(
            "INSERT INTO offer_items (offer_id, post_track_id, created_at) VALUES (?, ?, ?)",
            (offer_id, int(tid), now_iso()),
        )

    contributor_ids = _project_contributors(db, post["project_id"])
    for cid in sorted(contributor_ids):
        db.execute(
            """
            INSERT INTO offer_responses (offer_id, contributor_id, decision, decided_at)
            VALUES (?, ?, 'pending', NULL)
            """,
            (offer_id, cid),
        )

    # Insert collaborator_splits and notify each contributor
    _insert_splits_and_notify(db, offer_id, contributor_ids, total_amount)

    db.commit()
    flash("Offer submitted. Creators have been notified.")
    return redirect(url_for("marketplace.view_post", post_id=post_id))


@bp.route("/marketplace/offer/<int:offer_id>/decision", methods=["POST"])
@login_required
def decide_offer(offer_id):
    user = current_user()
    if user["role"] != "creator":
        flash("Only creators can respond to offers.")
        return redirect(url_for("marketplace.marketplace_index"))

    decision = request.form.get("decision", "").strip().lower()
    counter_raw = request.form.get("counter_amount", "").strip()
    if decision not in {"accepted", "rejected", "countered"}:
        flash("Invalid decision.")
        return redirect(request.referrer or url_for("marketplace.marketplace_index"))

    counter_amount = None
    if decision == "countered":
        try:
            counter_amount = float(counter_raw)
        except ValueError:
            flash("Counter amount must be numeric.")
            return redirect(request.referrer or url_for("marketplace.marketplace_index"))

    db = get_db()
    consent = db.execute(
        """
        SELECT * FROM offer_responses WHERE offer_id = ? AND contributor_id = ?
        """,
        (offer_id, user["id"]),
    ).fetchone()
    if not consent:
        flash("You are not a contributor on this offer.")
        return redirect(request.referrer or url_for("marketplace.marketplace_index"))

    db.execute(
        """
        UPDATE offer_responses
        SET decision = ?, counter_amount = ?, decided_at = ?
        WHERE offer_id = ? AND contributor_id = ?
        """,
        (decision, counter_amount, now_iso(), offer_id, user["id"]),
    )

    status = _offer_status_from_responses(db, offer_id)
    db.execute(
        "UPDATE offers SET status = ?, updated_at = ? WHERE id = ?",
        (status, now_iso(), offer_id),
    )

    # If fully accepted, create transactions for each split
    if status == "accepted":
        offer = db.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if offer:
            splits = db.execute(
                "SELECT * FROM collaborator_splits WHERE offer_id = ?", (offer_id,)
            ).fetchall()
            for split in splits:
                db.execute(
                    """
                    INSERT INTO transactions (offer_id, payer_id, payee_id, amount, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (offer_id, offer["producer_id"], split["contributor_id"], split["split_amount"], now_iso()),
                )

    db.commit()
    flash(f"Offer {decision}.")
    return redirect(request.referrer or url_for("marketplace.marketplace_index"))


@bp.route("/marketplace/offer/<int:offer_id>/accept-counter", methods=["POST"])
@login_required
def accept_counter(offer_id):
    user = current_user()
    if user["role"] != "producer":
        flash("Only producers can accept counters.")
        return redirect(url_for("marketplace.marketplace_index"))

    db = get_db()
    offer = db.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    if not offer or offer["producer_id"] != user["id"]:
        flash("Offer not found.")
        return redirect(url_for("marketplace.marketplace_index"))

    rows = db.execute(
        "SELECT counter_amount FROM offer_responses WHERE offer_id = ? AND decision = 'countered'",
        (offer_id,),
    ).fetchall()
    if not rows:
        flash("No counter to accept.")
        return redirect(request.referrer or url_for("marketplace.marketplace_index"))

    new_amount = max(row["counter_amount"] for row in rows if row["counter_amount"] is not None)
    db.execute(
        "UPDATE offers SET total_amount = ?, status = 'accepted', updated_at = ? WHERE id = ?",
        (new_amount, now_iso(), offer_id),
    )
    db.execute(
        "UPDATE offer_responses SET decision = 'accepted', decided_at = ? WHERE offer_id = ?",
        (now_iso(), offer_id),
    )

    # Create transactions for accepted counter
    contributor_ids = db.execute(
        "SELECT DISTINCT contributor_id FROM collaborator_splits WHERE offer_id = ?", (offer_id,)
    ).fetchall()
    if contributor_ids:
        split = new_amount / len(contributor_ids)
        for row in contributor_ids:
            db.execute(
                """
                INSERT INTO transactions (offer_id, payer_id, payee_id, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (offer_id, offer["producer_id"], row["contributor_id"], split, now_iso()),
            )

    db.commit()
    flash("Counter accepted. Offer marked accepted.")
    return redirect(request.referrer or url_for("marketplace.marketplace_index"))


@bp.route("/marketplace/package-bid", methods=["POST"])
@login_required
def package_bid():
    """Create a single offer covering multiple version IDs across multiple posts."""
    user = current_user()
    if user["role"] != "producer":
        flash("Only producers can make bids.")
        return redirect(url_for("marketplace.marketplace_index"))

    version_ids = request.form.getlist("version_ids[]")
    total_raw = request.form.get("total_amount", "0").strip()
    hours_raw = request.form.get("expires_hours", "24").strip()

    try:
        total_amount = float(total_raw)
    except ValueError:
        total_amount = 0.0
    try:
        hours = max(1, min(168, int(hours_raw)))
    except ValueError:
        hours = 24

    if not version_ids or total_amount <= 0:
        flash("Select at least one track and enter a valid bid amount.")
        return redirect(url_for("marketplace.marketplace_index"))

    db = get_db()

    # Resolve version_ids to post_track_ids
    # We need a post that contains these versions — create a synthetic package offer
    # by finding or building a common post. For simplicity, we require each version
    # to appear in at least one open post, and we attach to the first found post.
    first_post = None
    post_track_ids = []
    all_project_ids = set()

    for vid in version_ids:
        try:
            vid_int = int(vid)
        except ValueError:
            continue
        row = db.execute(
            """
            SELECT pt.id AS pt_id, po.id AS post_id, po.project_id
            FROM post_tracks pt
            JOIN posts po ON po.id = pt.post_id
            WHERE pt.version_id = ? AND po.status = 'open'
            LIMIT 1
            """,
            (vid_int,),
        ).fetchone()
        if row:
            post_track_ids.append(row["pt_id"])
            all_project_ids.add(row["project_id"])
            if first_post is None:
                first_post = row["post_id"]

    if not post_track_ids or first_post is None:
        flash("No open posts found for the selected tracks.")
        return redirect(url_for("marketplace.marketplace_index"))

    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(hours=hours)).isoformat(timespec="seconds")

    cur = db.execute(
        """
        INSERT INTO offers (post_id, producer_id, total_amount, status, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        """,
        (first_post, user["id"], total_amount, expires_at, now_iso(), now_iso()),
    )
    offer_id = cur.lastrowid

    for pt_id in post_track_ids:
        db.execute(
            "INSERT INTO offer_items (offer_id, post_track_id, created_at) VALUES (?, ?, ?)",
            (offer_id, pt_id, now_iso()),
        )

    # Collect all unique contributors across all projects
    all_contributor_ids = set()
    for pid in all_project_ids:
        cids = _project_contributors(db, pid)
        all_contributor_ids.update(cids)

    for cid in sorted(all_contributor_ids):
        db.execute(
            """
            INSERT OR IGNORE INTO offer_responses (offer_id, contributor_id, decision, decided_at)
            VALUES (?, ?, 'pending', NULL)
            """,
            (offer_id, cid),
        )

    _insert_splits_and_notify(db, offer_id, all_contributor_ids, total_amount)

    db.commit()
    flash("Package bid submitted.")
    return redirect(url_for("marketplace.marketplace_index"))
