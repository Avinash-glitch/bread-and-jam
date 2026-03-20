"""
jam.py — Live jam room signaling server

HOW WEBRTC SIGNALING WORKS
===========================
WebRTC peers connect directly to each other (P2P), but they first need to
exchange some metadata to set up that connection. This exchange is called
"signaling" and it goes through our server.

The metadata they exchange:
  1. SDP (Session Description Protocol) — describes what audio codecs each
     peer supports, sample rates, number of channels, etc.
     - The "offer" is created by whichever peer initiates the connection.
     - The "answer" is created by the peer that receives the offer.

  2. ICE candidates — network addresses/paths the peer can be reached at.
     ICE (Interactive Connectivity Establishment) tries multiple paths:
     local IP, public IP (discovered via STUN), and a relay (TURN server).
     We're using Google's public STUN server so no TURN needed for LAN/local.

TOPOLOGY: Full Mesh
===================
Each peer connects directly to every other peer. Simple and low-latency.
Works well for small sessions (2–4 people). For larger sessions you'd use
an SFU (Selective Forwarding Unit) but that's a separate server component.

   A ←──────────── direct audio ────────────→ B
   A ←──────────── direct audio ────────────→ C
   B ←──────────── direct audio ────────────→ C

ROOM MANAGEMENT
===============
We track who's in each project's jam room in a simple in-memory dict.
Key: "jam_{project_id}", Value: dict of {socket_sid: username}
When the server restarts, rooms are wiped — that's fine for dev.
For production you'd use Redis.
"""

from flask import Blueprint, jsonify, render_template, request, session
from flask_socketio import emit, join_room, leave_room

from . import socketio
from .db import get_db
from .utils import login_required

bp = Blueprint("jam", __name__)

# In-memory room registry: { room_name: { sid: username } }
_rooms: dict[str, dict[str, str]] = {}

# Listeners: { room_name: { sid: username } } — can hear but not transmit
_listeners: dict[str, dict[str, str]] = {}


def _room_name(project_id: int) -> str:
    return f"jam_{project_id}"


# ─── HTTP routes ───────────────────────────────────────────────────────────────

@bp.route("/projects/<int:project_id>/jam")
@login_required
def jam_room(project_id):
    db = get_db()
    project = db.execute(
        "SELECT p.*, u.username AS owner_name FROM projects p JOIN users u ON u.id = p.owner_id WHERE p.id = ?",
        (project_id,),
    ).fetchone()
    if not project:
        return "Project not found", 404

    user = db.execute("SELECT username FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    room = _room_name(project_id)
    peer_count = len(_rooms.get(room, {}))
    return render_template("jam.html", project=project, username=user["username"], peer_count=peer_count)


@bp.route("/jam/rooms")
@login_required
def jam_rooms_json():
    """Return JSON list of active jam rooms with basic info."""
    result = []
    for room_name, members in _rooms.items():
        if room_name.startswith("jam_"):
            try:
                project_id = int(room_name[4:])
            except ValueError:
                continue
            db = get_db()
            project = db.execute(
                "SELECT id, title FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            result.append({
                "room": room_name,
                "project_id": project_id,
                "project_title": project["title"] if project else f"Project {project_id}",
                "peer_count": len(members),
                "listener_count": len(_listeners.get(room_name, {})),
            })
    return jsonify(result)


@bp.route("/jam/lobby")
@login_required
def jam_lobby():
    """Render the jam lobby listing all active rooms."""
    active_rooms = []
    for room_name, members in _rooms.items():
        if room_name.startswith("jam_"):
            try:
                project_id = int(room_name[4:])
            except ValueError:
                continue
            db = get_db()
            project = db.execute(
                "SELECT id, title FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            active_rooms.append({
                "room": room_name,
                "project_id": project_id,
                "project_title": project["title"] if project else f"Project {project_id}",
                "peer_count": len(members),
                "listener_count": len(_listeners.get(room_name, {})),
            })
    return render_template("jam_lobby.html", title="Jam Lobby", active_rooms=active_rooms)


# ─── SocketIO events ──────────────────────────────────────────────────────────
#
# All events below run on the server whenever a client emits them.
# `request.sid` is the unique socket ID for the current client connection.
# It changes every time they reconnect, so it's safe to use as a peer ID.

@socketio.on("join_jam")
def handle_join_jam(data):
    """
    Client emits this when they open the jam page and are ready to connect.
    We tell them who's already in the room, then announce them to everyone else.
    """
    if "user_id" not in session:
        return

    project_id = int(data.get("project_id", 0))
    username = str(data.get("username", "Unknown"))[:40]
    room = _room_name(project_id)

    # Snapshot existing members BEFORE this peer joins
    existing = dict(_rooms.get(room, {}))

    join_room(room)  # adds this socket to the Socket.IO room for broadcasting

    if room not in _rooms:
        _rooms[room] = {}
    _rooms[room][request.sid] = username

    # Tell the newly joined peer: "here are the peers already waiting for you"
    # They will initiate WebRTC offers to each of these peers.
    emit("peers_in_room", {"peers": existing})

    # Tell everyone already in the room: "a new peer just joined, send them an answer"
    emit(
        "peer_joined",
        {"peer_id": request.sid, "username": username},
        to=room,
        include_self=False,
    )


@socketio.on("join_as_listener")
def handle_join_as_listener(data):
    """Join a room in listen-only mode — no peer_joined broadcast."""
    if "user_id" not in session:
        return

    project_id = int(data.get("project_id", 0))
    username = str(data.get("username", "Listener"))[:40]
    room = _room_name(project_id)

    join_room(room)

    if room not in _listeners:
        _listeners[room] = {}
    _listeners[room][request.sid] = username

    # Send current room state to the new listener
    existing = dict(_rooms.get(room, {}))
    emit("room_state", {"peers": existing, "listener_mode": True})


@socketio.on("request_to_join")
def handle_request_to_join(data):
    """A listener requests to become a full peer — ask the host."""
    if "user_id" not in session:
        return

    project_id = int(data.get("project_id", 0))
    username = str(data.get("username", "Unknown"))[:40]
    room = _room_name(project_id)

    peers_in_room = _rooms.get(room, {})
    if not peers_in_room:
        emit("join_denied", {"reason": "No host in room"})
        return

    # Emit to the first peer (treat as host)
    host_sid = next(iter(peers_in_room))
    emit(
        "join_request",
        {"requester_sid": request.sid, "username": username},
        to=host_sid,
    )


@socketio.on("approve_join")
def handle_approve_join(data):
    """Host approves a listener's request — promote to peer."""
    requester_sid = data.get("requester_sid")
    if not requester_sid:
        return

    # Find which room the requester is listening in
    for room_name, listener_map in list(_listeners.items()):
        if requester_sid in listener_map:
            username = listener_map.pop(requester_sid)
            if room_name not in _rooms:
                _rooms[room_name] = {}
            _rooms[room_name][requester_sid] = username

            # Snapshot existing peers (excluding the newly promoted)
            existing = {sid: u for sid, u in _rooms[room_name].items() if sid != requester_sid}
            emit("peers_in_room", {"peers": existing}, to=requester_sid)
            emit(
                "peer_joined",
                {"peer_id": requester_sid, "username": username},
                to=room_name,
                include_self=False,
            )
            break


@socketio.on("deny_join")
def handle_deny_join(data):
    """Host denies a listener's join request."""
    requester_sid = data.get("requester_sid")
    if requester_sid:
        emit("join_denied", {"reason": "Request denied by host"}, to=requester_sid)


@socketio.on("offer")
def handle_offer(data):
    """
    Peer A sends an SDP offer to Peer B.
    We relay it, adding A's socket ID so B knows who sent it.

    data = { to: "target_sid", sdp: <RTCSessionDescription> }
    """
    target = data.get("to")
    if target:
        emit("offer", {"from": request.sid, "sdp": data["sdp"]}, to=target)


@socketio.on("answer")
def handle_answer(data):
    """
    Peer B replies to Peer A's offer with an SDP answer.
    We relay it back.

    data = { to: "target_sid", sdp: <RTCSessionDescription> }
    """
    target = data.get("to")
    if target:
        emit("answer", {"from": request.sid, "sdp": data["sdp"]}, to=target)


@socketio.on("ice_candidate")
def handle_ice_candidate(data):
    """
    As ICE candidates are discovered locally (local IP, public IP via STUN,
    or TURN relay), each peer trickles them to the other side.
    We relay them as-is.

    data = { to: "target_sid", candidate: <RTCIceCandidateInit> }
    """
    target = data.get("to")
    if target:
        emit("ice_candidate", {"from": request.sid, "candidate": data["candidate"]}, to=target)


@socketio.on("disconnect")
def handle_disconnect():
    """
    When a socket disconnects (tab closed, network drop, etc.), remove them
    from our registry and notify the rest of the room.
    """
    # Clean up from peers
    for room, members in list(_rooms.items()):
        if request.sid in members:
            username = members.pop(request.sid)
            emit(
                "peer_left",
                {"peer_id": request.sid, "username": username},
                to=room,
            )
            if not members:
                del _rooms[room]
            break  # a socket is only ever in one jam room

    # Clean up from listeners
    for room, listeners in list(_listeners.items()):
        if request.sid in listeners:
            listeners.pop(request.sid)
            if not listeners:
                del _listeners[room]
            break
