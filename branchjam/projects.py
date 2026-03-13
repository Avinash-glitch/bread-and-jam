import os
import uuid
import json
import shutil

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from .audio_analysis import estimate_bpm, hum_to_instrument_wav, trim_wav_inplace, waveform_svg
from .db import get_db
from .utils import login_required, now_iso, save_upload


bp = Blueprint("projects", __name__)


def _is_wav_filename(filename):
    return bool(filename) and filename.lower().endswith(".wav")


def _project_contributor_ids(db, project_id):
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


def _is_download_authorized(db, requester_id, project_id, version_id):
    project = db.execute("SELECT owner_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        return False
    if requester_id == project["owner_id"]:
        return True

    req = db.execute(
        """
        SELECT * FROM download_requests
        WHERE requester_id = ? AND project_id = ? AND version_id = ? AND status = 'approved'
        """,
        (requester_id, project_id, version_id),
    ).fetchone()
    return req is not None


@bp.route("/projects", methods=["POST"])
@login_required
def create_project():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    bpm_raw = request.form.get("bpm", "").strip()
    time_signature = request.form.get("time_signature", "").strip() or "4/4"
    bars_raw = request.form.get("bars", "").strip()
    if not title or not bpm_raw or not bars_raw:
        flash("Project title, BPM, and bars are required.")
        return redirect(url_for("social.dashboard"))
    try:
        project_bpm = float(bpm_raw)
        bars = int(bars_raw)
    except ValueError:
        flash("BPM must be numeric and bars must be a whole number.")
        return redirect(url_for("social.dashboard"))
    if project_bpm < 40 or project_bpm > 240:
        flash("BPM must be between 40 and 240.")
        return redirect(url_for("social.dashboard"))
    if bars <= 0:
        flash("Bars must be greater than 0.")
        return redirect(url_for("social.dashboard"))

    stem = save_upload(request.files.get("stem"))
    if request.files.get("stem") and not stem:
        flash("Unsupported file format.")
        return redirect(url_for("social.dashboard"))
    if not stem:
        flash("Initial stem file is required.")
        return redirect(url_for("social.dashboard"))

    stem_path = os.path.join(current_app.config["UPLOAD_DIR"], stem)
    try:
        if _is_wav_filename(stem):
            detected_bpm = estimate_bpm(stem_path)
            svg = waveform_svg(stem_path)
        else:
            detected_bpm = None
            svg = None
    except ValueError as exc:
        os.remove(stem_path)
        flash(str(exc))
        return redirect(url_for("social.dashboard"))

    if detected_bpm is not None:
        tolerance = current_app.config["TEMPO_TOLERANCE_BPM"]
        if abs(detected_bpm - project_bpm) > tolerance:
            os.remove(stem_path)
            flash(
                f"Upload disqualified: detected BPM {detected_bpm:.1f} is off-tempo from project BPM {project_bpm:.1f}."
            )
            return redirect(url_for("social.dashboard"))

    db = get_db()
    cur = db.execute(
        """
        INSERT INTO projects (owner_id, title, description, stem_file, bpm, time_signature, bars, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (session["user_id"], title, description, stem, project_bpm, time_signature, bars, now_iso()),
    )
    project_id = cur.lastrowid

    cur = db.execute(
        """
        INSERT INTO branches (project_id, parent_branch_id, name, creator_id, created_at)
        VALUES (?, NULL, 'main', ?, ?)
        """,
        (project_id, session["user_id"], now_iso()),
    )
    main_branch_id = cur.lastrowid

    db.execute(
        """
        INSERT INTO versions (
            branch_id, version_number, notes, file_path, uploaded_by_user_id, detected_bpm, waveform_svg, created_at
        )
        VALUES (?, 1, 'Initial upload', ?, ?, ?, ?, ?)
        """,
        (main_branch_id, stem, session["user_id"], detected_bpm, svg, now_iso()),
    )
    db.commit()
    flash("Project created with main branch.")
    return redirect(url_for("projects.project_detail", project_id=project_id))


@bp.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    db = get_db()
    project = db.execute(
        """
        SELECT p.*, u.username as owner_name
        FROM projects p JOIN users u ON u.id = p.owner_id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    if not project:
        return "Project not found", 404

    branches = db.execute(
        """
        SELECT b.*, u.username AS creator_name
        FROM branches b
        JOIN users u ON u.id = b.creator_id
        WHERE b.project_id = ?
        ORDER BY b.created_at
        """,
        (project_id,),
    ).fetchall()

    versions_by_branch = {}
    for branch in branches:
        versions_by_branch[branch["id"]] = db.execute(
            """
            SELECT v.*, u.username AS uploader_name
            FROM versions v
            LEFT JOIN users u ON u.id = v.uploaded_by_user_id
            WHERE v.branch_id = ?
            ORDER BY version_number DESC
            """,
            (branch["id"],),
        ).fetchall()

    branch_by_id = {b["id"]: b for b in branches}

    def lineage_ids(branch):
        ids = []
        seen = set()
        cur = branch
        while cur and cur["id"] not in seen:
            ids.append(cur["id"])
            seen.add(cur["id"])
            cur = branch_by_id.get(cur["parent_branch_id"])
        return list(reversed(ids))

    lineage_tracks = {}
    for branch in branches:
        track_rows = []
        for bid in lineage_ids(branch):
            versions = versions_by_branch.get(bid) or []
            if not versions:
                continue
            version_rows = []
            for v in reversed(versions):
                if not v["file_path"]:
                    continue
                version_rows.append(
                    {
                        "version_id": v["id"],
                        "version_number": v["version_number"],
                        "notes": v["notes"],
                        "uploader_name": v["uploader_name"],
                        "file_path": v["file_path"],
                        "waveform_svg": v["waveform_svg"],
                        "url": url_for("projects.uploaded_file", filename=v["file_path"]),
                    }
                )
            if version_rows:
                track_rows.append(
                    {
                        "branch_id": bid,
                        "branch_name": branch_by_id[bid]["name"],
                        "versions": version_rows,
                    }
                )
        lineage_tracks[branch["id"]] = track_rows

    return render_template(
        "project_detail.html",
        title=project["title"],
        project=project,
        branches=branches,
        versions_by_branch=versions_by_branch,
        lineage_tracks=lineage_tracks,
    )


@bp.route("/projects/<int:project_id>/grid")
@login_required
def project_grid_editor(project_id):
    db = get_db()
    project = db.execute(
        """
        SELECT p.*, u.username AS owner_name
        FROM projects p
        JOIN users u ON u.id = p.owner_id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    if not project:
        return "Project not found", 404

    state_row = db.execute(
        "SELECT * FROM project_grid_states WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    version_rows = db.execute(
        """
        SELECT
          v.id,
          v.version_number,
          v.notes,
          v.file_path,
          b.name AS branch_name
        FROM versions v
        JOIN branches b ON b.id = v.branch_id
        WHERE b.project_id = ? AND v.file_path IS NOT NULL
        ORDER BY v.created_at DESC
        """,
        (project_id,),
    ).fetchall()
    available_clips = []
    for row in version_rows:
        label = f"{row['branch_name']} v{row['version_number']}"
        if row["notes"]:
            label = f"{label} - {row['notes']}"
        available_clips.append(
            {
                "version_id": row["id"],
                "label": label,
                "url": url_for("projects.uploaded_file", filename=row["file_path"]),
            }
        )

    default_tracks = [
        {"name": "Kick", "sound": "kick", "steps": [1 if i in (0, 8) else 0 for i in range(16)], "volume": 0.9},
        {"name": "Snare", "sound": "snare", "steps": [1 if i in (4, 12) else 0 for i in range(16)], "volume": 0.8},
        {"name": "HiHat", "sound": "hihat", "steps": [1 if i % 2 == 0 else 0 for i in range(16)], "volume": 0.5},
        {"name": "Bass", "sound": "bass", "steps": [1 if i in (0, 3, 8, 11) else 0 for i in range(16)], "volume": 0.65},
        {"name": "Lead", "sound": "lead", "steps": [1 if i in (2, 6, 10, 14) else 0 for i in range(16)], "volume": 0.5},
    ]
    default_clips = []
    for clip in available_clips[:6]:
        default_clips.append(
            {
                "version_id": clip["version_id"],
                "label": clip["label"],
                "url": clip["url"],
                "steps": [1 if i == 0 else 0 for i in range(16)],
                "volume": 0.8,
                "trim_start": 0.0,
                "trim_end": 0.0,
            }
        )
    grid_state = {"tracks": default_tracks, "clips": default_clips}
    steps = 16
    bpm = float(project["bpm"] or 120.0)

    if state_row:
        bpm = float(state_row["bpm"])
        steps = int(state_row["steps"])
        try:
            grid_state = json.loads(state_row["state_json"])
        except json.JSONDecodeError:
            pass

    return render_template(
        "project_grid.html",
        title=f"{project['title']} Grid",
        project=project,
        bpm=bpm,
        steps=steps,
        grid_state=grid_state,
        available_clips=available_clips,
    )


@bp.route("/projects/<int:project_id>/grid/save", methods=["POST"])
@login_required
def save_project_grid(project_id):
    payload = request.get_json(silent=True) or {}
    try:
        bpm = float(payload.get("bpm", 120))
        steps = int(payload.get("steps", 16))
        tracks = payload.get("tracks", [])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid payload"}), 400

    if bpm < 40 or bpm > 240:
        return jsonify({"ok": False, "error": "BPM out of range"}), 400
    if steps < 8 or steps > 64:
        return jsonify({"ok": False, "error": "Steps out of range"}), 400
    if not isinstance(tracks, list) or not tracks:
        return jsonify({"ok": False, "error": "At least one track is required"}), 400

    normalized_tracks = []
    for track in tracks[:12]:
        name = str(track.get("name", "Track")).strip()[:30] or "Track"
        sound = str(track.get("sound", "lead")).strip()[:20] or "lead"
        volume = track.get("volume", 0.7)
        try:
            volume = max(0.0, min(1.0, float(volume)))
        except (TypeError, ValueError):
            volume = 0.7
        raw_steps = track.get("steps", [])
        step_values = []
        for i in range(steps):
            val = 0
            if isinstance(raw_steps, list) and i < len(raw_steps):
                val = 1 if raw_steps[i] else 0
            step_values.append(val)
        normalized_tracks.append({"name": name, "sound": sound, "volume": volume, "steps": step_values})

    clip_entries = payload.get("clips", [])
    if not isinstance(clip_entries, list):
        clip_entries = []

    normalized_clips = []
    for clip in clip_entries[:20]:
        version_id = clip.get("version_id")
        try:
            version_id = int(version_id)
        except (TypeError, ValueError):
            continue
        label = str(clip.get("label", "Clip")).strip()[:80] or "Clip"
        url = str(clip.get("url", "")).strip()[:500]
        volume = clip.get("volume", 0.8)
        trim_start = clip.get("trim_start", 0.0)
        trim_end = clip.get("trim_end", 0.0)
        try:
            volume = max(0.0, min(1.0, float(volume)))
        except (TypeError, ValueError):
            volume = 0.8
        try:
            trim_start = max(0.0, float(trim_start))
        except (TypeError, ValueError):
            trim_start = 0.0
        try:
            trim_end = max(0.0, float(trim_end))
        except (TypeError, ValueError):
            trim_end = 0.0
        raw_steps = clip.get("steps", [])
        step_values = []
        for i in range(steps):
            val = 0
            if isinstance(raw_steps, list) and i < len(raw_steps):
                val = 1 if raw_steps[i] else 0
            step_values.append(val)
        normalized_clips.append(
            {
                "version_id": version_id,
                "label": label,
                "url": url,
                "volume": volume,
                "trim_start": trim_start,
                "trim_end": trim_end,
                "steps": step_values,
            }
        )

    db = get_db()
    project = db.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404

    state_json = json.dumps({"tracks": normalized_tracks, "clips": normalized_clips})
    existing = db.execute(
        "SELECT id FROM project_grid_states WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if existing:
        db.execute(
            """
            UPDATE project_grid_states
            SET bpm = ?, steps = ?, state_json = ?, updated_by_user_id = ?, updated_at = ?
            WHERE project_id = ?
            """,
            (bpm, steps, state_json, session["user_id"], now_iso(), project_id),
        )
    else:
        db.execute(
            """
            INSERT INTO project_grid_states (project_id, bpm, steps, state_json, updated_by_user_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, bpm, steps, state_json, session["user_id"], now_iso()),
        )
    db.commit()
    return jsonify({"ok": True})


@bp.route("/projects/<int:project_id>/branches", methods=["POST"])
@login_required
def create_branch(project_id):
    name = request.form.get("name", "").strip()
    parent_branch_id = request.form.get("parent_branch_id")
    parent = int(parent_branch_id) if parent_branch_id else None
    if not name:
        flash("Branch name required.")
        return redirect(url_for("projects.project_detail", project_id=project_id))

    db = get_db()
    project = db.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        flash("Project not found.")
        return redirect(url_for("social.dashboard"))

    if parent is not None:
        exists = db.execute(
            "SELECT id FROM branches WHERE id = ? AND project_id = ?", (parent, project_id)
        ).fetchone()
        if not exists:
            flash("Parent branch not found in this project.")
            return redirect(url_for("projects.project_detail", project_id=project_id))

    db.execute(
        """
        INSERT INTO branches (project_id, parent_branch_id, name, creator_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, parent, name, session["user_id"], now_iso()),
    )
    db.commit()
    flash("Branch created.")
    return redirect(url_for("projects.project_detail", project_id=project_id))


@bp.route("/branches/<int:branch_id>/versions", methods=["POST"])
@login_required
def add_version(branch_id):
    notes = request.form.get("notes", "").strip()
    file_name = save_upload(request.files.get("audio"))
    if not file_name:
        flash("Audio file required and must be supported.")
        return redirect(request.referrer or url_for("social.dashboard"))

    db = get_db()
    branch = db.execute("SELECT * FROM branches WHERE id = ?", (branch_id,)).fetchone()
    if not branch:
        flash("Branch not found.")
        return redirect(url_for("social.dashboard"))
    project = db.execute("SELECT id, bpm FROM projects WHERE id = ?", (branch["project_id"],)).fetchone()
    if not project:
        flash("Project not found.")
        return redirect(url_for("social.dashboard"))

    audio_path = os.path.join(current_app.config["UPLOAD_DIR"], file_name)
    try:
        if _is_wav_filename(file_name):
            detected_bpm = estimate_bpm(audio_path)
            svg = waveform_svg(audio_path)
        else:
            detected_bpm = None
            svg = None
    except ValueError as exc:
        os.remove(audio_path)
        flash(str(exc))
        return redirect(url_for("projects.project_detail", project_id=branch["project_id"]))

    # Legacy projects created before BPM support may have NULL bpm.
    # Backfill from the first detected upload so tempo validation can proceed.
    if detected_bpm is not None:
        project_bpm = project["bpm"]
        if project_bpm is None:
            project_bpm = detected_bpm
            db.execute("UPDATE projects SET bpm = ? WHERE id = ?", (project_bpm, project["id"]))

        tolerance = current_app.config["TEMPO_TOLERANCE_BPM"]
        if abs(detected_bpm - float(project_bpm)) > tolerance:
            os.remove(audio_path)
            flash(
                f"Upload disqualified: detected BPM {detected_bpm:.1f} is off-tempo from project BPM {float(project_bpm):.1f}."
            )
            return redirect(url_for("projects.project_detail", project_id=branch["project_id"]))

    latest = db.execute(
        "SELECT COALESCE(MAX(version_number), 0) as max_v FROM versions WHERE branch_id = ?",
        (branch_id,),
    ).fetchone()
    next_version = latest["max_v"] + 1
    db.execute(
        """
        INSERT INTO versions (
            branch_id, version_number, notes, file_path, uploaded_by_user_id, detected_bpm, waveform_svg, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            branch_id,
            next_version,
            notes or f"Version {next_version}",
            file_name,
            session["user_id"],
            detected_bpm,
            svg,
            now_iso(),
        ),
    )
    db.commit()
    flash(f"Recorded version {next_version}.")
    return redirect(url_for("projects.project_detail", project_id=branch["project_id"]))


@bp.route("/branches/<int:branch_id>/hum-to-instrument", methods=["POST"])
@login_required
def hum_to_instrument_version(branch_id):
    notes = request.form.get("notes", "").strip()
    instrument = request.form.get("instrument", "sine").strip().lower()
    if instrument not in {"sine", "square", "saw"}:
        flash("Invalid instrument selection.")
        return redirect(request.referrer or url_for("social.dashboard"))

    hum_file_name = save_upload(request.files.get("hum_audio"))
    if not hum_file_name:
        flash("Hum audio file required and must be WAV.")
        return redirect(request.referrer or url_for("social.dashboard"))

    db = get_db()
    branch = db.execute("SELECT * FROM branches WHERE id = ?", (branch_id,)).fetchone()
    if not branch:
        flash("Branch not found.")
        return redirect(url_for("social.dashboard"))
    project = db.execute("SELECT id, bpm FROM projects WHERE id = ?", (branch["project_id"],)).fetchone()
    if not project:
        flash("Project not found.")
        return redirect(url_for("social.dashboard"))

    hum_path = os.path.join(current_app.config["UPLOAD_DIR"], hum_file_name)
    generated_name = f"hum_{instrument}_{uuid.uuid4().hex[:10]}.wav"
    generated_path = os.path.join(current_app.config["UPLOAD_DIR"], generated_name)

    try:
        hum_to_instrument_wav(hum_path, generated_path, instrument=instrument)
        detected_bpm = estimate_bpm(generated_path)
        svg = waveform_svg(generated_path)
    except ValueError as exc:
        if os.path.exists(generated_path):
            os.remove(generated_path)
        if os.path.exists(hum_path):
            os.remove(hum_path)
        flash(str(exc))
        return redirect(url_for("projects.project_detail", project_id=branch["project_id"]))

    if os.path.exists(hum_path):
        os.remove(hum_path)

    project_bpm = project["bpm"]
    if project_bpm is None:
        project_bpm = detected_bpm
        db.execute("UPDATE projects SET bpm = ? WHERE id = ?", (project_bpm, project["id"]))

    tolerance = current_app.config["TEMPO_TOLERANCE_BPM"]
    if abs(detected_bpm - float(project_bpm)) > tolerance:
        os.remove(generated_path)
        flash(
            f"Generated track disqualified: detected BPM {detected_bpm:.1f} is off-tempo from project BPM {float(project_bpm):.1f}."
        )
        return redirect(url_for("projects.project_detail", project_id=branch["project_id"]))

    latest = db.execute(
        "SELECT COALESCE(MAX(version_number), 0) as max_v FROM versions WHERE branch_id = ?",
        (branch_id,),
    ).fetchone()
    next_version = latest["max_v"] + 1
    db.execute(
        """
        INSERT INTO versions (
            branch_id, version_number, notes, file_path, uploaded_by_user_id, detected_bpm, waveform_svg, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            branch_id,
            next_version,
            notes or f"Hum to {instrument} conversion",
            generated_name,
            session["user_id"],
            detected_bpm,
            svg,
            now_iso(),
        ),
    )
    db.commit()
    flash(f"Generated version {next_version} from hum melody.")
    return redirect(url_for("projects.project_detail", project_id=branch["project_id"]))


@bp.route("/versions/<int:version_id>/download")
@login_required
def download_version(version_id):
    db = get_db()
    version = db.execute(
        """
        SELECT v.*, b.project_id
        FROM versions v
        JOIN branches b ON b.id = v.branch_id
        WHERE v.id = ?
        """,
        (version_id,),
    ).fetchone()
    if not version:
        flash("Version not found.")
        return redirect(url_for("social.dashboard"))

    authorized = _is_download_authorized(db, session["user_id"], version["project_id"], version_id)
    if not authorized:
        flash("Download blocked. Request contributor consent first.")
        return redirect(url_for("projects.request_download", version_id=version_id))
    return send_from_directory(
        current_app.config["UPLOAD_DIR"], version["file_path"], as_attachment=True
    )


@bp.route("/versions/<int:version_id>/trim", methods=["POST"])
@login_required
def destructive_trim_version(version_id):
    start_s = request.form.get("trim_start", "").strip()
    end_s = request.form.get("trim_end", "").strip()
    try:
        start_s = float(start_s) if start_s else 0.0
        end_s = float(end_s) if end_s else 0.0
    except ValueError:
        flash("Trim values must be numeric seconds.")
        return redirect(request.referrer or url_for("social.dashboard"))

    db = get_db()
    version = db.execute(
        """
        SELECT v.*, b.project_id, p.owner_id
        FROM versions v
        JOIN branches b ON b.id = v.branch_id
        JOIN projects p ON p.id = b.project_id
        WHERE v.id = ?
        """,
        (version_id,),
    ).fetchone()
    if not version:
        flash("Version not found.")
        return redirect(url_for("social.dashboard"))

    if session["user_id"] not in {version["uploaded_by_user_id"], version["owner_id"]}:
        flash("Only the uploader or project owner can destructively edit this audio.")
        return redirect(url_for("projects.project_detail", project_id=version["project_id"]))

    file_path = os.path.join(current_app.config["UPLOAD_DIR"], version["file_path"])
    if not os.path.exists(file_path):
        flash("Audio file not found on disk.")
        return redirect(url_for("projects.project_detail", project_id=version["project_id"]))

    if not _is_wav_filename(version["file_path"]):
        flash("Trim currently supports WAV files only.")
        return redirect(url_for("projects.project_detail", project_id=version["project_id"]))

    try:
        trim_wav_inplace(file_path, start_s, end_s)
        detected_bpm = estimate_bpm(file_path)
        svg = waveform_svg(file_path)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("projects.project_detail", project_id=version["project_id"]))

    db.execute(
        """
        UPDATE versions
        SET detected_bpm = ?, waveform_svg = ?
        WHERE id = ?
        """,
        (detected_bpm, svg, version_id),
    )
    db.commit()
    flash("Audio trimmed and replaced for this version.")
    return redirect(url_for("projects.project_detail", project_id=version["project_id"]))


@bp.route("/versions/<int:version_id>/add-to/<artist>", methods=["POST"])
@login_required
def add_version_to_artist(version_id, artist):
    artist = (artist or "").strip().lower()
    if artist not in {"artist1", "artist2"}:
        flash("Unknown artist target.")
        return redirect(request.referrer or url_for("social.dashboard"))

    db = get_db()
    version = db.execute(
        """
        SELECT v.*, b.project_id
        FROM versions v
        JOIN branches b ON b.id = v.branch_id
        WHERE v.id = ?
        """,
        (version_id,),
    ).fetchone()
    if not version or not version["file_path"]:
        flash("Version not found or missing file.")
        return redirect(request.referrer or url_for("social.dashboard"))

    src_path = os.path.join(current_app.config["UPLOAD_DIR"], version["file_path"])
    if not os.path.exists(src_path):
        flash("Audio file not found on disk.")
        return redirect(request.referrer or url_for("social.dashboard"))

    dest_dir = os.path.join(current_app.config["UPLOAD_DIR"], artist)
    os.makedirs(dest_dir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(version["file_path"]))
    dest_name = f"{base}{ext}"
    dest_path = os.path.join(dest_dir, dest_name)
    if os.path.exists(dest_path):
        dest_name = f"{base}_{uuid.uuid4().hex[:6]}{ext}"
        dest_path = os.path.join(dest_dir, dest_name)

    shutil.copy2(src_path, dest_path)
    flash(f"Added track to {artist}: {dest_name}")
    return redirect(request.referrer or url_for("projects.project_detail", project_id=version["project_id"]))


@bp.route("/versions/<int:version_id>/request-download")
@login_required
def request_download(version_id):
    db = get_db()
    version = db.execute(
        """
        SELECT v.id, v.file_path, b.project_id
        FROM versions v
        JOIN branches b ON b.id = v.branch_id
        WHERE v.id = ?
        """,
        (version_id,),
    ).fetchone()
    if not version:
        flash("Version not found.")
        return redirect(url_for("social.dashboard"))

    if _is_download_authorized(db, session["user_id"], version["project_id"], version_id):
        return redirect(url_for("projects.download_version", version_id=version_id))

    existing = db.execute(
        """
        SELECT id, status FROM download_requests
        WHERE requester_id = ? AND version_id = ?
        """,
        (session["user_id"], version_id),
    ).fetchone()
    if existing:
        flash(f"Download request already exists ({existing['status']}).")
        return redirect(url_for("social.dashboard"))

    cur = db.execute(
        """
        INSERT INTO download_requests (requester_id, project_id, version_id, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (session["user_id"], version["project_id"], version_id, now_iso()),
    )
    request_id = cur.lastrowid

    contributor_ids = _project_contributor_ids(db, version["project_id"])
    for cid in sorted(contributor_ids):
        decision = "approved" if cid == session["user_id"] else "pending"
        decided_at = now_iso() if decision == "approved" else None
        db.execute(
            """
            INSERT INTO download_request_consents (request_id, contributor_id, decision, decided_at)
            VALUES (?, ?, ?, ?)
            """,
            (request_id, cid, decision, decided_at),
        )

    db.commit()
    flash("Download request submitted. Waiting for contributor consent.")
    return redirect(url_for("social.dashboard"))


@bp.route("/download-requests/<int:request_id>/decision", methods=["POST"])
@login_required
def decide_download_request(request_id):
    action = request.form.get("action", "").strip().lower()
    if action not in {"approved", "rejected"}:
        flash("Invalid decision.")
        return redirect(url_for("social.dashboard"))

    db = get_db()
    consent = db.execute(
        """
        SELECT * FROM download_request_consents
        WHERE request_id = ? AND contributor_id = ?
        """,
        (request_id, session["user_id"]),
    ).fetchone()
    if not consent:
        flash("You are not a contributor for this request.")
        return redirect(url_for("social.dashboard"))

    db.execute(
        """
        UPDATE download_request_consents
        SET decision = ?, decided_at = ?
        WHERE request_id = ? AND contributor_id = ?
        """,
        (action, now_iso(), request_id, session["user_id"]),
    )

    states = db.execute(
        "SELECT decision FROM download_request_consents WHERE request_id = ?",
        (request_id,),
    ).fetchall()
    decisions = [row["decision"] for row in states]
    if "rejected" in decisions:
        status = "rejected"
    elif all(d == "approved" for d in decisions):
        status = "approved"
    else:
        status = "pending"
    db.execute("UPDATE download_requests SET status = ? WHERE id = ?", (status, request_id))
    db.commit()

    flash(f"Download request {action}.")
    return redirect(url_for("social.dashboard"))


@bp.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(current_app.config["UPLOAD_DIR"], filename, as_attachment=False)
