import sqlite3

from flask import current_app, g


def _ensure_column(db, table_name, column_name, definition):
    columns = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {row["name"] for row in columns}
    if column_name not in existing:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS friend_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL,
            UNIQUE(sender_id, receiver_id),
            FOREIGN KEY(sender_id) REFERENCES users(id),
            FOREIGN KEY(receiver_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            stem_file TEXT,
            bpm REAL,
            time_signature TEXT,
            bars INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            parent_branch_id INTEGER,
            name TEXT NOT NULL,
            creator_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id),
            FOREIGN KEY(parent_branch_id) REFERENCES branches(id),
            FOREIGN KEY(creator_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            notes TEXT,
            file_path TEXT,
            uploaded_by_user_id INTEGER,
            detected_bpm REAL,
            waveform_svg TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(branch_id) REFERENCES branches(id),
            FOREIGN KEY(uploaded_by_user_id) REFERENCES users(id),
            UNIQUE(branch_id, version_number)
        );

        CREATE TABLE IF NOT EXISTS rules_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS download_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            version_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
            created_at TEXT NOT NULL,
            UNIQUE(requester_id, version_id),
            FOREIGN KEY(requester_id) REFERENCES users(id),
            FOREIGN KEY(project_id) REFERENCES projects(id),
            FOREIGN KEY(version_id) REFERENCES versions(id)
        );

        CREATE TABLE IF NOT EXISTS download_request_consents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            contributor_id INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('pending', 'approved', 'rejected')),
            decided_at TEXT,
            UNIQUE(request_id, contributor_id),
            FOREIGN KEY(request_id) REFERENCES download_requests(id),
            FOREIGN KEY(contributor_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS project_grid_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL UNIQUE,
            bpm REAL NOT NULL,
            steps INTEGER NOT NULL,
            state_json TEXT NOT NULL,
            updated_by_user_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id),
            FOREIGN KEY(updated_by_user_id) REFERENCES users(id)
        );
        """
    )

    # Lightweight migration path for existing local DB files.
    _ensure_column(db, "projects", "bpm", "REAL")
    _ensure_column(db, "projects", "time_signature", "TEXT")
    _ensure_column(db, "projects", "bars", "INTEGER")
    _ensure_column(db, "versions", "uploaded_by_user_id", "INTEGER")
    _ensure_column(db, "versions", "detected_bpm", "REAL")
    _ensure_column(db, "versions", "waveform_svg", "TEXT")
    db.commit()
