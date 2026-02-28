import os
import random
import requests
from flask import Flask, redirect, request, session, jsonify, render_template
from urllib.parse import urlencode

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI          = os.environ.get("REDIRECT_URI")

SPOTIFY_AUTH_URL  = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE  = "https://api.spotify.com/v1"

SCOPES = "user-read-playback-state user-modify-playback-state"

YEAR_MIN = 1940
YEAR_MAX = 2025
HEBREW_CHANCE = 0.30

HEBREW_SEARCH_TERMS = [
    "ישראלי", "עברית", "מוזיקה ישראלית",
    "israeli pop", "israeli rock", "mizrahi", "mizrachit",
]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    params = {
        "client_id":     SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPES,
        "show_dialog":   True,
    }
    return redirect(f"{SPOTIFY_AUTH_URL}?{urlencode(params)}")


@app.route("/callback")
def callback():
    code  = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return redirect("/?error=access_denied")

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        return redirect("/?error=token_exchange_failed")

    tokens = resp.json()
    session["access_token"]  = tokens["access_token"]
    session["refresh_token"] = tokens.get("refresh_token")
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


def refresh_access_token():
    rt = session.get("refresh_token")
    if not rt:
        return False
    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": rt,
            "client_id":     SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code == 200:
        tokens = resp.json()
        session["access_token"] = tokens["access_token"]
        if "refresh_token" in tokens:
            session["refresh_token"] = tokens["refresh_token"]
        return True
    return False


def auth_headers():
    return {"Authorization": f"Bearer {session['access_token']}"}


def spotify_get(endpoint, params=None):
    resp = requests.get(f"{SPOTIFY_API_BASE}{endpoint}", headers=auth_headers(), params=params)
    if resp.status_code == 401 and refresh_access_token():
        resp = requests.get(f"{SPOTIFY_API_BASE}{endpoint}", headers=auth_headers(), params=params)
    return resp


def spotify_put(endpoint, body=None):
    resp = requests.put(
        f"{SPOTIFY_API_BASE}{endpoint}",
        headers={**auth_headers(), "Content-Type": "application/json"},
        json=body,
    )
    if resp.status_code == 401 and refresh_access_token():
        resp = requests.put(
            f"{SPOTIFY_API_BASE}{endpoint}",
            headers={**auth_headers(), "Content-Type": "application/json"},
            json=body,
        )
    return resp


def spotify_post(endpoint, body=None):
    resp = requests.post(
        f"{SPOTIFY_API_BASE}{endpoint}",
        headers={**auth_headers(), "Content-Type": "application/json"},
        json=body,
    )
    if resp.status_code == 401 and refresh_access_token():
        resp = requests.post(
            f"{SPOTIFY_API_BASE}{endpoint}",
            headers={**auth_headers(), "Content-Type": "application/json"},
            json=body,
        )
    return resp


def search_songs(year, hebrew=False):
    candidates = []

    if hebrew:
        term  = random.choice(HEBREW_SEARCH_TERMS)
        query = f"{term} year:{year}"
        offsets = [0, 20, 40]
    else:
        query   = f"year:{year}"
        offsets = [0, 20, 40, 60, 80]

    for offset in offsets:
        resp = spotify_get("/search", params={
            "q":      query,
            "type":   "track",
            "limit":  50,
            "offset": offset,
            "market": "IL",
        })
        if resp.status_code != 200:
            continue

        tracks = resp.json().get("tracks", {}).get("items", [])
        for t in tracks:
            if not t or not t.get("id"):
                continue
            release    = t.get("album", {}).get("release_date", "")
            track_year = release[:4] if release else ""
            if track_year != str(year):
                continue
            if not hebrew and t.get("popularity", 0) < 20:
                continue
            candidates.append(t)

        if len(candidates) >= 20:
            break

    return candidates


def format_track(t, year):
    album  = t.get("album", {})
    images = album.get("images", [])
    return {
        "id":          t["id"],
        "uri":         t["uri"],
        "name":        t["name"],
        "artists":     [a["name"] for a in t.get("artists", [])],
        "album":       album.get("name", ""),
        "album_image": images[0]["url"] if images else None,
        "year":        str(year),
    }


@app.route("/api/status")
def status():
    if "access_token" not in session:
        return jsonify({"logged_in": False})
    resp = spotify_get("/me")
    if resp.status_code != 200:
        session.clear()
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "display_name": resp.json().get("display_name", "")})


@app.route("/api/play_random")
def play_random():
    if "access_token" not in session:
        return jsonify({"error": "Not logged in"}), 401

    last_id  = session.get("last_track_id")
    hebrew   = random.random() < HEBREW_CHANCE
    year     = random.randint(YEAR_MIN, YEAR_MAX)

    # Try up to 5 different years if search comes up empty
    candidates = []
    for _ in range(5):
        candidates = search_songs(year, hebrew=hebrew)
        if candidates:
            break
        year = random.randint(YEAR_MIN, YEAR_MAX)

    if not hebrew and not candidates:
        candidates = search_songs(year, hebrew=False)

    if not candidates:
        return jsonify({"error": "Could not find songs. Please try again."}), 500

    pool  = [t for t in candidates if t["id"] != last_id] if len(candidates) > 1 else candidates
    track = random.choice(pool)
    session["last_track_id"] = track["id"]

    # Get devices
    resp    = spotify_get("/me/player/devices")
    devices = resp.json().get("devices", []) if resp.status_code == 200 else []
    if not devices:
        return jsonify({"error": "No active Spotify device found. Open Spotify on your phone or desktop first."}), 404

    device    = next((d for d in devices if d.get("is_active")), devices[0])
    play_resp = spotify_put(f"/me/player/play?device_id={device['id']}", {"uris": [track["uri"]]})

    if play_resp.status_code not in (200, 204):
        return jsonify({"error": f"Could not start playback (status {play_resp.status_code}). Make sure Spotify is open."}), 500

    return jsonify({"success": True, "track": format_track(track, year)})


@app.route("/api/pause", methods=["POST"])
def pause():
    if "access_token" not in session:
        return jsonify({"error": "Not logged in"}), 401
    resp = spotify_put("/me/player/pause")
    if resp.status_code in (200, 204):
        return jsonify({"success": True, "state": "paused"})
    return jsonify({"error": "Could not pause"}), 500


@app.route("/api/resume", methods=["POST"])
def resume():
    if "access_token" not in session:
        return jsonify({"error": "Not logged in"}), 401
    resp = spotify_put("/me/player/play")
    if resp.status_code in (200, 204):
        return jsonify({"success": True, "state": "playing"})
    return jsonify({"error": "Could not resume"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
