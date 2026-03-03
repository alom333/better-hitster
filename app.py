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

# ─── Tune these ─────────────────────────────────────────────────
ROCK_BIAS       = 0.0   # 0.0 = balanced, 1.0 = always rock
POPULARITY_POOL = 50    # lower = more famous songs (min 10, max 100)
STACK_SIZE      = 5     # songs pre-fetched in background
YEAR_TOLERANCE  = 3     # ±years accepted around the target year
# ────────────────────────────────────────────────────────────────

# Decade tags on Last.fm — hugely populated with well-known songs
DECADE_TAGS = {
    1940: ["40s"],
    1950: ["50s"],
    1960: ["60s"],
    1970: ["70s"],
    1980: ["80s"],
    1990: ["90s"],
    2000: ["2000s"],
    2010: ["2010s"],
    2020: ["2020s"],
}

GENRE_TAGS = ["pop", "rock", "soul", "jazz", "country", "rnb",
              "classic rock", "folk", "disco", "punk", "hip-hop", "electronic"]

COMPILATION_KEYWORDS = [
    "greatest hits", "best of", "collection", "remaster", "remastered",
    "anthology", "platinum", "gold", "essential", "ultimate", "definitive",
    "anniversary", "deluxe", "legacy", "years", "historia",
    "very best", "all time", "hits collection", "the singles",
]

song_stack    = deque()
stack_lock    = threading.Lock()
stack_filling = False

_cc_token_cache = {"token": None, "expires_at": 0}


# ── Helpers ──────────────────────────────────────────────────────

def year_to_decade_tag(year):
    decade_start = (year // 10) * 10
    return DECADE_TAGS.get(decade_start, ["60s"])[0]


def is_compilation(album_name):
    n = album_name.lower()
    return any(kw in n for kw in COMPILATION_KEYWORDS)


def get_client_credentials_token():
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
            _cc_token_cache["token"]      = d["access_token"]
            _cc_token_cache["expires_at"] = now + d.get("expires_in", 3600)
            return _cc_token_cache["token"]
    except Exception as e:
        logging.error(f"CC token failed: {e}")
    return None


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


def sp_put(url, token, **kwargs):
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return requests.put(url, headers=hdrs, **kwargs)


def get_lastfm_year(artist, title):
    """
    Ask Last.fm track.getInfo for the real original release year.
    This is our ground truth — we never use Spotify's year.
    Returns an int year or None.
    """
    try:
        r = requests.get(LASTFM_API_BASE, params={
            "method":  "track.getInfo",
            "artist":  artist,
            "track":   title,
            "api_key": LASTFM_API_KEY,
            "format":  "json",
        }, timeout=6)
        if not r.ok:
            return None
        data = r.json()

        # Source 1: wiki published date e.g. "14 Oct 1978, 00:00"
        published = data.get("track", {}).get("wiki", {}).get("published", "")
        if published:
            parts = published.strip().split(" ")
            if len(parts) >= 3:
                y = parts[2].replace(",", "")
                if y.isdigit() and 1930 <= int(y) <= 2025:
                    logging.debug(f"Last.fm wiki year: {artist} - {title} => {y}")
                    return int(y)

        # Source 2: album releasedate field
        releasedate = data.get("track", {}).get("album", {}).get("releasedate", "")
        if releasedate:
            parts = releasedate.strip().split(" ")
            if len(parts) >= 3:
                y = parts[2].replace(",", "")
                if y.isdigit() and 1930 <= int(y) <= 2025:
                    logging.debug(f"Last.fm album year: {artist} - {title} => {y}")
                    return int(y)

        return None
    except Exception as e:
        logging.debug(f"get_lastfm_year failed: {e}")
        return None


def get_spotify_track(artist, title, token):
    """
    Search Spotify for the track.
    Returns (track_uri, album_art, sp_artist, sp_title) or None on failure.
    We do NOT use Spotify's year at all — only URI and art.
    """
    try:
        r = requests.get(f"{SPOTIFY_API_BASE}/search",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "q":     f"track:{title} artist:{artist}",
                "type":  "track",
                "limit": 10,
            }, timeout=8)
        if not r.ok:
            return None

        items = r.json().get("tracks", {}).get("items", [])
        if not items:
            return None

        # Prefer non-compilation albums — but only for choosing which result to use
        real = [t for t in items if not is_compilation(t["album"]["name"])]
        best = real[0] if real else items[0]

        album_art = best["album"]["images"][0]["url"] if best["album"]["images"] else None
        return {
            "track_uri": best["uri"],
            "album_art": album_art,
            "sp_artist": best["artists"][0]["name"],
            "sp_title":  best["name"],
        }
    except Exception as e:
        logging.debug(f"get_spotify_track failed: {e}")
        return None


def build_one_song(target_year=None):
    """
    Find one song, optionally targeting a specific year.
    Year is ALWAYS determined by Last.fm track.getInfo — never Spotify.
    """
    if target_year is None:
        target_year = random.randint(*YEAR_RANGE)

    decade_tag = year_to_decade_tag(target_year)

    # We'll try decade tag first, then genre tags as fallback
    tags_to_try = [decade_tag]
    if ROCK_BIAS > 0 and random.random() < ROCK_BIAS:
        tags_to_try = ["rock"] + tags_to_try
    else:
        tags_to_try += [random.choice(GENRE_TAGS)]

    cc_token = get_client_credentials_token()
    if not cc_token:
        logging.error("No CC token available")
        return None

    best_song    = None   # best candidate so far (closest year)
    best_yr_diff = 9999

    for tag in tags_to_try:
        for page in [1, 2]:
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
                logging.error(f"Last.fm failed: {e}")
                continue

            if not lfm.ok:
                continue

            tracks = lfm.json().get("tracks", {}).get("track", [])
            if not tracks:
                continue

            pool = tracks[:POPULARITY_POOL]
            random.shuffle(pool)

            for t in pool[:15]:
                artist = t.get("artist", {}).get("name", "")
                title  = t.get("name", "")
                if not artist or not title:
                    continue

                # Get the real year from Last.fm FIRST
                real_year = get_lastfm_year(artist, title)
                if real_year is None:
                    logging.debug(f"No Last.fm year for {artist} - {title}, skipping")
                    continue

                yr_diff = abs(real_year - target_year)
                logging.debug(f"{artist} - {title}: real_year={real_year}, target={target_year}, diff={yr_diff}")

                # Perfect match — use it immediately
                if yr_diff <= YEAR_TOLERANCE:
                    sp = get_spotify_track(artist, title, cc_token)
                    if sp:
                        logging.debug(f"✓ Found match: {artist} - {title} ({real_year})")
                        return {
                            "year":      str(real_year),
                            "artist":    sp["sp_artist"],
                            "title":     sp["sp_title"],
                            "album_art": sp["album_art"],
                            "track_uri": sp["track_uri"],
                        }

                # Keep track of closest song found so far as fallback
                if yr_diff < best_yr_diff:
                    sp = get_spotify_track(artist, title, cc_token)
                    if sp:
                        best_yr_diff = yr_diff
                        best_song = {
                            "year":      str(real_year),
                            "artist":    sp["sp_artist"],
                            "title":     sp["sp_title"],
                            "album_art": sp["album_art"],
                            "track_uri": sp["track_uri"],
                        }

    # Nothing within tolerance — return closest found (still correct year from Last.fm)
    if best_song:
        logging.debug(f"Using closest match: {best_song['artist']} - {best_song['title']} ({best_song['year']}, diff={best_yr_diff})")
        return best_song

    logging.warning(f"build_one_song failed for target_year={target_year}")
    return None


def fill_stack_background():
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
                    logging.debug(f"Stack now: {len(song_stack)}")
    finally:
        with stack_lock:
            stack_filling = False


def ensure_stack_filling():
    with stack_lock:
        size = len(song_stack)
    if size < STACK_SIZE:
        t = threading.Thread(target=fill_stack_background, daemon=True)
        t.start()


# ── Routes ───────────────────────────────────────────────────────

@app.route("/")
def index():
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

    # Pop from pre-fetched stack
    song = None
    with stack_lock:
        if song_stack:
            song = song_stack.popleft()
            logging.debug(f"Popped from stack. Stack now: {len(song_stack)}")

    # Stack empty — build on demand (spinner shows)
    if song is None:
        logging.warning("Stack empty — building on demand")
        song = build_one_song()

    # Always refill stack in background
    ensure_stack_filling()

    if not song:
        return jsonify({"error": "could_not_find_song"}), 500

    # Play on Spotify using user's token
    token = session.get("access_token")
    play  = sp_put(
        f"{SPOTIFY_API_BASE}/me/player/play",
        token=token,
        json={"uris": [song["track_uri"]]},
    )
    if play.status_code == 401 and try_refresh():
        token = session.get("access_token")
        play  = sp_put(
            f"{SPOTIFY_API_BASE}/me/player/play",
            token=token,
            json={"uris": [song["track_uri"]]},
        )

    logging.debug(f"Play {play.status_code}: {song['artist']} - {song['title']} ({song['year']})")

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
