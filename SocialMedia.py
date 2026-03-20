from branchjam import create_app, socketio

app = create_app()

if __name__ == "__main__":
    # socketio.run() replaces app.run() so that WebSocket connections work.
    # Under the hood it still runs the Flask dev server, just with WebSocket support layered on.
    socketio.run(app, debug=True)
