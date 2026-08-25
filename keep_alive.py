"""
Minimal Flask keep-alive server, run in a background thread.

Matches the pattern from your other Discord bots (Anime TCG, BirthdayGoat) —
Render's free web-service tier expects something bound to a port, and this
also gives you a cheap uptime-ping target for services like UptimeRobot.
"""

import os
import threading

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "AM Track is running."


def _run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
