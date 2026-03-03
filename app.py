import os
import random
import requests
import urllib.parse
import logging
import threading
from collections import deque
from flask import Flask, redirect, request, session, jsonify, render_template

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

CLIENT_ID      = os.environ.get("SPOTIFY_CLIENT_ID")
CLIENT_SECRET  = os.environ.get("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI   = os.environ.get("REDIRECT_URI")
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")

SPOTIFY_AUTH_URL  = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE  = "https://api.spotify.com/v1"
LASTFM_API_BASE   = "http://ws.audioscrobbler.com/2.0/"

YEAR_RANGE = (1940, 2025)

# ─── Tune these to your taste ───────────────────────────────────

# 0.0 = fully balanced  |  1.0 = always rock
ROCK_BIAS = 0.0

# How many top tracks to pull from per tag (lower = more popular songs)
# 10  = only the biggest hits
# 50  = well-known songs
# 200 = deeper cuts
POPULARITY_POOL = 50

# How many songs to pre-fetch and keep ready in the stack
STACK_SIZE = 5

# ────────────────────────────────────────────────────────────────

TAGS = ["pop", "rock", "soul", "jazz", "country", "rnb",
        "classic rock", "folk", "disco", "punk", "hip-hop", "electronic"]

# Global pre-fetch stack (list of song dicts, ready to serve)
song_stack = deque()
stack_lock = threading.Lock()
stack_filling = False


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


def sp_get(url, token, **kwargs):
    hdrs = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=hdrs, **kwargs)
    return r


def sp_put(url, token, **kwargs):
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.put(url, headers=hdrs, **kwargs)
    return r


def get_original_year(artist, title):
    """Ask Last.fm for the original release year of a track."""
    try:
        r = requests.get(LASTFM_API_BASE, params={
            "method":   "track.getInfo",
            "artist":   artist,
            "track":    title,
            "api_key":  LASTFM_API_KEY,
            "format":   "json",
        }, timeout=6)
        if not r.ok:
            return None
        data = r.json()
        wiki = data.get("track", {}).get("wiki", {})
        published = wiki.get("published", "")
        # Last.fm wiki published date looks like "01 Jan 1979, 00:00"
        if published:
            parts = published.strip().split(" ")
            if len(parts) >= 3:
                year_str = parts[2].replace(",", "")
                if year_str.isdigit() and 1900 <= int(year_str) <= 2025:
                    return year_str
        # Fallback: check album release date from Last.fm
        album = data.get("track", {}).get("album", {})
        if album:
            # Last.fm doesn't always give year here, but try
            pass
        return None
    except Exception as e:
        logging.debug(f"get_original_year failed: {e}")
        return None


def get_spotify_original_year(artist, title, token):
    """
    Search Spotify for the track and find the earliest release year
    across all albums (not just the first result).
    This avoids getting a 2002 compilation when the song is from 1979.
    """
    try:
        r = requests.get(f"{SPOTIFY_API_BASE}/search",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "q":     f"track:{title} artist:{artist}",
                "type":  "track",
                "limit": 10,   # get more results to find the earliest
            }, timeout=8)
        if not r.ok:
            return None, None, None, None
        items = r.json().get("tracks", {}).get("items", [])
        if not items:
            return None, None, None, None

        # Find the track with the earliest release date
        best_track = None
        best_year = 9999
        for track in items:
            release = track["album"].get("release_date", "")
            if release:
                yr = int(release[:4])
                if yr < best_year:
                    best_year = yr
                    best_track = track

        if not best_track:
            best_track = items[0]
            best_year = int(best_track["album"].get("release_date", "2000")[:4])

        sp_artist = best_track["artists"][0]["name"]
        sp_title  = best_track["name"]
        album_art = best_track["album"]["images"][0]["url"] if best_track["album"]["images"] else None
        track_uri = best_track["uri"]

        return str(best_year), sp_artist, sp_title, album_art, track_uri

    except Exception as e:
        logging.debug(f"get_spotify_original_year failed: {e}")
        return None, None, None, None, None


def build_one_song():
    """
    Find one valid song (Last.fm → Spotify) and return it as a dict.
    Does NOT play it — just prepares the metadata.
    Returns None if nothing found after attempts.
    """
    for attempt in range(8):
        use_rock = ROCK_BIAS > 0 and random.random() < ROCK_BIAS
        tag  = "rock" if use_rock else random.choice(TAGS)
        page = random.randint(1, max(1, POPULARITY_POOL // 100 + 1))

        try:
            lfm = requests.get(LASTFM_API_BASE, params={
                "method":  "tag.gettoptracks",
                "tag":     tag,
                "api_key": LASTFM_API_KEY,
                "format":  "json",
                "limit":   min(POPULARITY_POOL, 100),
                "page":    page,
            }, timeout=10)
        except Exception as e:
            logging.error(f"Last.fm request failed: {e}")
            continue

        if not lfm.ok:
            logging.error(f"Last.fm {lfm.status_code}")
            continue

        tracks = lfm.json().get("tracks", {}).get("track", [])
        if not tracks:
            continue

        # Limit to POPULARITY_POOL
        pool = tracks[:POPULARITY_POOL]
        random.shuffle(pool)

        for t in pool[:8]:
            artist = t.get("artist", {}).get("name", "")
            title  = t.get("name", "")
            if not artist or not title:
                continue

            # We need a token to search Spotify — use a client credentials token
            # (no user auth needed for search)
            cc_token = get_client_credentials_token()
            if not cc_token:
                logging.error("Could not get client credentials token")
                return None

            result = get_spotify_original_year(artist, title, cc_token)
            if len(result) != 5 or result[0] is None:
                continue

            sp_year, sp_artist, sp_title, album_art, track_uri = result

            # Validate year range
            if not (YEAR_RANGE[0] <= int(sp_year) <= YEAR_RANGE[1]):
                continue

            # Try Last.fm for a more accurate original year
            lastfm_year = get_original_year(sp_artist, sp_title)
            if lastfm_year and YEAR_RANGE[0] <= int(lastfm_year) <= YEAR_RANGE[1]:
                final_year = lastfm_year
            else:
                final_year = sp_year

            logging.debug(f"Stack built: {sp_artist} - {sp_title} ({final_year})")
            return {
                "year":      final_year,
                "artist":    sp_artist,
                "title":     sp_title,
                "album_art": album_art,
                "track_uri": track_uri,
            }

    return None


_cc_token_cache = {"token": None, "expires_at": 0}

def get_client_credentials_token():
    """Get a Spotify app token (no user needed) for searching."""
    import time
    now = time.time()
    if _cc_token_cache["token"] and now < _cc_token_cache["expires_at"] - 60:
        return _cc_token_cache["token"]
    try:
        r = requests.post(SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(CLIENT_ID, CLIENT_SECRET),
            timeout=8)
        if r.ok:
            d = r.json()
            _cc_token_cache["token"] = d["access_token"]
            _cc_token_cache["expires_at"] = now + d.get("expires_in", 3600)
            return _cc_token_cache["token"]
    except Exception as e:
        logging.error(f"CC token failed: {e}")
    return None


def fill_stack_background():
    """Fill the song stack up to STACK_SIZE in a background thread."""
    global stack_filling
    with stack_lock:
        if stack_filling:
            return
        stack_filling = True
    try:
        while True:
            with stack_lock:
                current_size = len(song_stack)
            if current_size >= STACK_SIZE:
                break
            song = build_one_song()
            if song:
                with stack_lock:
                    song_stack.append(song)
                    logging.debug(f"Stack size now: {len(song_stack)}")
    finally:
        with stack_lock:
            stack_filling = False


def ensure_stack_filling():
    """Kick off background fill if stack is low."""
    with stack_lock:
        size = len(song_stack)
    if size < STACK_SIZE:
        t = threading.Thread(target=fill_stack_background, daemon=True)
        t.start()


# ── Routes ───────────────────────────────────────────────────────

@app.route("/")
def index():
    # Start filling the stack as soon as someone loads the page
    ensure_stack_filling()
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
    ensure_stack_filling()
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

    # Try to pop from pre-fetched stack
    song = None
    with stack_lock:
        if song_stack:
            song = song_stack.popleft()
            logging.debug(f"Popped from stack. Stack now has {len(song_stack)}")

    # If stack was empty, build one now (slower, fallback)
    if song is None:
        logging.warning("Stack was empty — building song on demand")
        song = build_one_song()

    # Kick off background refill
    ensure_stack_filling()

    if not song:
        return jsonify({"error": "could_not_find_song"}), 500

    # Now play it using the user's token
    token = session.get("access_token")
    play = sp_put(
        f"{SPOTIFY_API_BASE}/me/player/play",
        token=token,
        json={"uris": [song["track_uri"]]},
    )

    # Handle token expiry for playback
    if play.status_code == 401:
        if try_refresh():
            token = session.get("access_token")
            play = sp_put(
                f"{SPOTIFY_API_BASE}/me/player/play",
                token=token,
                json={"uris": [song["track_uri"]]},
            )

    logging.debug(f"Play response: {play.status_code} for {song['artist']} - {song['title']} ({song['year']})")

    return jsonify({
        "year":        song["year"],
        "artist":      song["artist"],
        "title":       song["title"],
        "album_art":   song["album_art"],
        "track_uri":   song["track_uri"],
        "played":      play.status_code in [200, 204],
        "play_status": play.status_code,
    })


@app.route("/api/pause", methods=["POST"])
def pause():
    if "access_token" not in session:
        return jsonify({"error": "not_logged_in"}), 401
    token = session.get("access_token")
    r = sp_put(f"{SPOTIFY_API_BASE}/me/player/pause", token=token)
    if r.status_code == 401 and try_refresh():
        r = sp_put(f"{SPOTIFY_API_BASE}/me/player/pause", token=session.get("access_token"))
    return jsonify({"status": r.status_code})


@app.route("/api/resume", methods=["POST"])
def resume():
    if "access_token" not in session:
        return jsonify({"error": "not_logged_in"}), 401
    token = session.get("access_token")
    r = sp_put(f"{SPOTIFY_API_BASE}/me/player/play", token=token)
    if r.status_code == 401 and try_refresh():
        r = sp_put(f"{SPOTIFY_API_BASE}/me/player/play", token=session.get("access_token"))
    return jsonify({"status": r.status_code})


@app.route("/api/playback-state")
def playback_state():
    if "access_token" not in session:
        return jsonify({"error": "not_logged_in"}), 401
    token = session.get("access_token")
    r = requests.get(f"{SPOTIFY_API_BASE}/me/player",
                     headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 204 or not r.text:
        return jsonify({"is_playing": False, "no_device": True})
    return jsonify(r.json())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
