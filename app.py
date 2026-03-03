import os
import random
import requests
import urllib.parse
import logging
from flask import Flask, redirect, request, session, jsonify, render_template

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI  = os.environ.get("REDIRECT_URI")
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")

SPOTIFY_AUTH_URL  = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE  = "https://api.spotify.com/v1"
LASTFM_API_BASE   = "http://ws.audioscrobbler.com/2.0/"

YEAR_RANGE = (1940, 2025)

# ─── Genre bias ─────────────────────────────────────────────────
# 0.0 = fully balanced  |  1.0 = always rock
ROCK_BIAS = 0.0
# ────────────────────────────────────────────────────────────────

TAGS = ["pop", "rock", "soul", "jazz", "country", "rnb",
        "classic rock", "folk", "disco", "punk", "hip-hop", "electronic"]


def sp_headers():
    return {"Authorization": f"Bearer {session.get('access_token')}"}


def try_refresh():
    rt = session.get("refresh_token")
    if not rt:
        return False
    r = requests.post(SPOTIFY_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "refresh_token": rt,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    if r.ok:
        session["access_token"] = r.json()["access_token"]
        return True
    return False


def sp_get(url, **kwargs):
    r = requests.get(url, headers=sp_headers(), **kwargs)
    if r.status_code == 401 and try_refresh():
        r = requests.get(url, headers=sp_headers(), **kwargs)
    return r


def sp_put(url, **kwargs):
    hdrs = {**sp_headers(), "Content-Type": "application/json"}
    r = requests.put(url, headers=hdrs, **kwargs)
    if r.status_code == 401 and try_refresh():
        r = requests.put(url, headers=hdrs, **kwargs)
    return r


# ── Routes ───────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", logged_in="access_token" in session)


@app.route("/login")
def login():
    scope = (
        "user-modify-playback-state "
        "user-read-playback-state "
        "user-read-email "
        "user-read-private"
    )
    params = {
        "client_id":     CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         scope,
        "show_dialog":   "true",
    }
    return redirect(SPOTIFY_AUTH_URL + "?" + urllib.parse.urlencode(params))


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect("/?error=auth_failed")
    r = requests.post(SPOTIFY_TOKEN_URL, data={
        "grant_type":    "authorization_code",
        "code":           code,
        "redirect_uri":   REDIRECT_URI,
        "client_id":      CLIENT_ID,
        "client_secret":  CLIENT_SECRET,
    })
    if not r.ok:
        return redirect("/?error=token_failed")
    tokens = r.json()
    session["access_token"]  = tokens["access_token"]
    session["refresh_token"] = tokens.get("refresh_token")
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/api/random-song")
def random_song():
    if "access_token" not in session:
        return jsonify({"error": "not_logged_in"}), 401

    if not LASTFM_API_KEY:
        logging.error("LASTFM_API_KEY is not set!")
        return jsonify({"error": "lastfm_key_missing"}), 500

    for attempt in range(6):
        year = random.randint(*YEAR_RANGE)
        use_rock = ROCK_BIAS > 0 and random.random() < ROCK_BIAS
        tag = "rock" if use_rock else random.choice(TAGS)
        page = random.randint(1, 5)

        logging.debug(f"Attempt {attempt+1}: year={year}, tag={tag}, page={page}")

        try:
            lfm = requests.get(LASTFM_API_BASE, params={
                "method":  "tag.gettoptracks",
                "tag":     tag,
                "api_key": LASTFM_API_KEY,
                "format":  "json",
                "limit":   100,
                "page":    page,
            }, timeout=10)
        except Exception as e:
            logging.error(f"Last.fm request failed: {e}")
            continue

        if not lfm.ok:
            logging.error(f"Last.fm returned {lfm.status_code}: {lfm.text[:200]}")
            continue

        data = lfm.json()
        tracks = data.get("tracks", {}).get("track", [])
        logging.debug(f"Last.fm returned {len(tracks)} tracks")

        if not tracks:
            logging.warning(f"No tracks for tag={tag} page={page}")
            continue

        random.shuffle(tracks)

        for t in tracks[:10]:
            artist = t.get("artist", {}).get("name", "")
            title  = t.get("name", "")
            if not artist or not title:
                continue

            logging.debug(f"Trying: {artist} - {title}")

            try:
                sp = sp_get(f"{SPOTIFY_API_BASE}/search", params={
                    "q":     f"track:{title} artist:{artist}",
                    "type":  "track",
                    "limit": 3,
                })
            except Exception as e:
                logging.error(f"Spotify search failed: {e}")
                continue

            if not sp.ok:
                logging.error(f"Spotify search returned {sp.status_code}")
                continue

            items = sp.json().get("tracks", {}).get("items", [])
            if not items:
                continue

            track     = items[0]
            track_uri = track["uri"]
            sp_artist = track["artists"][0]["name"]
            sp_title  = track["name"]
            album_art = track["album"]["images"][0]["url"] if track["album"]["images"] else None
            release   = track["album"].get("release_date", str(year))
            sp_year   = release[:4] if release else str(year)

            if not (YEAR_RANGE[0] <= int(sp_year) <= YEAR_RANGE[1]):
                logging.debug(f"Year {sp_year} out of range, skipping")
                continue

            play = sp_put(
                f"{SPOTIFY_API_BASE}/me/player/play",
                json={"uris": [track_uri]},
            )

            logging.debug(f"Play response: {play.status_code}")

            return jsonify({
                "year":        sp_year,
                "artist":      sp_artist,
                "title":       sp_title,
                "album_art":   album_art,
                "track_uri":   track_uri,
                "played":      play.status_code in [200, 204],
                "play_status": play.status_code,
            })

    logging.error("All 6 attempts failed to find a song")
    return jsonify({"error": "could_not_find_song"}), 500


@app.route("/api/pause", methods=["POST"])
def pause():
    if "access_token" not in session:
        return jsonify({"error": "not_logged_in"}), 401
    r = sp_put(f"{SPOTIFY_API_BASE}/me/player/pause")
    return jsonify({"status": r.status_code})


@app.route("/api/resume", methods=["POST"])
def resume():
    if "access_token" not in session:
        return jsonify({"error": "not_logged_in"}), 401
    r = sp_put(f"{SPOTIFY_API_BASE}/me/player/play")
    return jsonify({"status": r.status_code})


@app.route("/api/playback-state")
def playback_state():
    if "access_token" not in session:
        return jsonify({"error": "not_logged_in"}), 401
    r = sp_get(f"{SPOTIFY_API_BASE}/me/player")
    if r.status_code == 204 or not r.text:
        return jsonify({"is_playing": False, "no_device": True})
    return jsonify(r.json())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
