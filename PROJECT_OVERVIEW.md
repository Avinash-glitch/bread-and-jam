# BranchJam — Project Overview

A collaborative music platform where musicians create projects, build on each
other's stems across git-style branches, negotiate licensing in a marketplace,
and jam together live.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python / Flask |
| Realtime | Flask-SocketIO (threading mode) |
| Database | SQLite (`branchjam.db`) |
| Audio analysis | Custom (`audio_analysis.py`) |
| Frontend | Jinja2 templates + vanilla JS |
| Entry point | `SocialMedia.py` → `socketio.run(app)` |

---

## Blueprints

| Blueprint | File | Prefix |
|---|---|---|
| `auth` | `branchjam/auth.py` | `/register`, `/login`, `/logout` |
| `social` | `branchjam/social.py` | `/`, `/dashboard`, `/rules-chat`, `/feed` |
| `projects` | `branchjam/projects.py` | `/projects`, `/branches`, `/versions` |
| `marketplace` | `branchjam/marketplace.py` | `/marketplace` |
| `jam` | `branchjam/jam.py` | `/projects/<id>/jam` |

---

## Features

### Auth (`auth.py`)
- Register with username + password + role (`creator` or `producer`)
- Login / logout with session cookie
- Passwords hashed via Werkzeug

### Social (`social.py`)
- **Dashboard** — lists all projects, friend activity feed (last 7 days), pending
  download consent requests, and your own download request statuses
- **Friend requests** — send, accept, reject
- **Rules Chat** — a shared chat room (persistent, DB-backed)
- **Feed actions** — like / forward / share / collaborate / bid on a version

### Projects (`projects.py`)

#### Project
- Create a project with: title, description, BPM (40–240), time signature,
  bars, and an initial stem file
- BPM is validated against the uploaded file (±3 BPM tolerance) using
  `estimate_bpm()` — WAV only; non-WAV skips validation
- Supported upload formats: WAV, MP3, M4A, AAC, FLAC, OGG, Opus, AIFF
- Max upload size: 100 MB

#### Branches
- Every project starts with a `main` branch auto-created on project creation
- Any user can create additional branches, optionally parented to an existing branch
- Branches form a lineage tree — the project detail page resolves the full
  ancestor chain for each branch to assemble the layered stem view

#### Versions
- Upload a new audio file to any branch → creates the next version number
- WAV uploads get BPM detection + SVG waveform generated and stored
- Non-WAV uploads skip BPM/waveform (stored as-is)
- Version uploader or project owner can **destructively trim** a WAV version
  (in-place, updates waveform + BPM in DB)
- **Hum-to-Instrument**: upload a hummed WAV → server converts pitch to a
  sine / square / saw synth WAV and saves it as a new version

#### Downloads & Contributor Consent
- Project owner can download any version freely
- Everyone else must submit a **download request**
- All contributors (owner + anyone who uploaded a version) must individually
  approve — any single rejection blocks the download
- Consent decisions tracked in `download_request_consents`

#### Grid Editor (`/projects/<id>/grid`)
- Step sequencer per project — 8–64 steps, 5 built-in drum/synth tracks
- Up to 6 audio clips (from project versions) can be placed on the grid
- Volume + per-clip trim (start/end) supported
- State saved as JSON in `project_grid_states` table (one row per project)

### Marketplace (`marketplace.py`)
- **Creators** list projects/versions as posts with per-track prices
- **Producers** browse open posts and make offers (total amount + expiry)
- **Contributor consent** — every contributor on the project must accept/reject/counter
- Counter-offers: producers can accept the highest counter to close the deal
- Offer status rolls up from individual responses (majority reject → rejected,
  all accept → accepted, any counter → countered)

### Live Jam (`jam.py` + `jam.html`)
- Per-project jam room at `/projects/<id>/jam`
- **Signaling server** via Flask-SocketIO:
  - `join_jam` — client announces itself; server sends back existing peers
    and notifies the room
  - `offer` / `answer` — SDP relay between peers
  - `ice_candidate` — ICE candidate trickle relay
  - `disconnect` — cleans up room membership, notifies peers
- **Topology**: full mesh P2P (each peer connects directly to every other peer)
- Room state held in-memory (`_rooms` dict); resets on server restart
- STUN: Google's public server (`stun:stun.l.google.com:19302`)
- Audio: browser `getUserMedia` → WebRTC `RTCPeerConnection`
- Local monitoring only (no return-stream monitoring — by design, to avoid echo)

---

## Data Model

```
users
  id, username, password_hash, role, created_at

friend_requests
  id, sender_id, receiver_id, status, created_at

projects
  id, owner_id, title, description, stem_file,
  bpm, time_signature, bars, created_at

branches
  id, project_id, parent_branch_id, name, creator_id, created_at

versions
  id, branch_id, version_number, notes, file_path,
  uploaded_by_user_id, detected_bpm, waveform_svg, created_at

rules_chat_messages
  id, user_id, message, created_at

download_requests
  id, requester_id, project_id, version_id, status, created_at

download_request_consents
  id, request_id, contributor_id, decision, decided_at

project_grid_states
  id, project_id, bpm, steps, state_json, updated_by_user_id, updated_at

posts
  id, project_id, creator_id, title, description, status, created_at

post_tracks
  id, post_id, version_id, price, created_at

offers
  id, post_id, producer_id, total_amount, status, expires_at,
  created_at, updated_at

offer_items
  id, offer_id, post_track_id, created_at

offer_responses
  id, offer_id, contributor_id, decision, counter_amount, decided_at
```

---

## File Layout

```
SocialMediaPlugin/
├── SocialMedia.py              # entry point
├── branchjam.db                # SQLite database (gitignored)
├── uploads/                    # uploaded audio files (gitignored)
├── branchjam/
│   ├── __init__.py             # app factory, SocketIO init
│   ├── auth.py
│   ├── social.py
│   ├── projects.py
│   ├── marketplace.py
│   ├── jam.py                  # WebRTC signaling + SocketIO events
│   ├── db.py                   # get_db, init_db, migrations
│   ├── utils.py                # login_required, save_upload, now_iso
│   ├── audio_analysis.py       # estimate_bpm, waveform_svg, hum_to_instrument_wav, trim_wav_inplace
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── register.html
│       ├── dashboard.html
│       ├── project_detail.html
│       ├── project_grid.html
│       ├── jam.html            # WebRTC client UI
│       ├── marketplace.html
│       ├── create_post.html
│       ├── post_detail.html
│       └── rules_chat.html
└── PROJECT_OVERVIEW.md
```

---

## Known Constraints & Next Steps

- **HTTPS required for WebRTC mic access** on non-localhost devices (use ngrok
  or `ssl_context="adhoc"` for iPad testing)
- Jam rooms are in-memory — use Redis for multi-worker / production
- Full-mesh WebRTC works well up to ~4 peers; beyond that an SFU is needed
- Audio upload BPM validation is WAV-only; other formats bypass the check
- No background job runner yet — offer expiry is not auto-enforced
- WebRTC jam: Opus codec at 10ms frames planned for lowest browser latency
