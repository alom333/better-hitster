import os
import random
import requests
import base64
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

app = FastAPI()
templates = Jinja2Templates(directory="templates")

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
PLAYLIST_ID = os.getenv("PLAYLIST_ID")

sessions = {}


def basic_auth_header():
    encoded = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return f"Basic {encoded}"


def fetch_all_playlist_tracks(token: str):
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100"
    headers = {"Authorization": f"Bearer {token}"}

    while url:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"[ERROR] Playlist fetch failed {res.status_code}: {res.text}")
            return []
        data = res.json()
        for item in data.get("items", []):
            track = item.get("track")
            if track and track.get("id") and not track.get("is_local"):
                tracks.append(track)
        url = data.get("next")

    print(f"[INFO] Loaded {len(tracks)} tracks from playlist.")
    return tracks


def refresh_token(refresh_tok: str):
    res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_tok},
        headers={
            "Authorization": basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=10
    )
    data = res.json()
    return data.get("access_token")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def home(request: Request):
    logged_in = "user" in sessions
    return templates.TemplateResponse("index.html", {"request": request, "logged_in": logged_in})


@app.get("/me")
def me():
    return {"logged_in": "user" in sessions}


@app.get("/login")
def login():
    scope = " ".join([
        "streaming",
        "user-read-email",
        "user-read-private",
        "user-modify-playback-state",
        "user-read-playback-state",
        "playlist-read-private",
        "playlist-read-collaborative",
    ])
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": scope,
        "redirect_uri": REDIRECT_URI,
        "show_dialog": True,
    }
    return RedirectResponse("https://accounts.spotify.com/authorize?" + urlencode(params))


@app.get("/callback")
def callback(code: str = None, error: str = None):
    if error or not code:
        return RedirectResponse("/?error=auth_denied")

    res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={
            "Authorization": basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=10,
    )
    data = res.json()

    if "access_token" not in data:
        print(f"[ERROR] Token exchange failed: {data}")
        return RedirectResponse("/?error=token_failed")

    sessions["user"] = {
        "token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "tracks": [],
        "current_track": None,
    }
    return RedirectResponse("/")


@app.get("/play")
def play():
    user = sessions.get("user")
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    token = user["token"]

    # Load tracks if not loaded yet
    if not user["tracks"]:
        user["tracks"] = fetch_all_playlist_tracks(token)

    # If still empty, try refreshing the token
    if not user["tracks"] and user.get("refresh_token"):
        new_token = refresh_token(user["refresh_token"])
        if new_token:
            user["token"] = new_token
            token = new_token
            user["tracks"] = fetch_all_playlist_tracks(token)

    if not user["tracks"]:
        return JSONResponse({"error": "Could not load playlist. Check that your Spotify account is added to the app's User Management in the Spotify Developer Dashboard."}, status_code=500)

    track = random.choice(user["tracks"])
    user["current_track"] = track

    # Start playback
    play_res = requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"uris": [track["uri"]]},
        timeout=5,
    )

    if play_res.status_code == 404:
        return JSONResponse({"error": "No active Spotify device. Open Spotify on your phone or desktop first, then try again."}, status_code=404)

    if play_res.status_code == 403:
        return JSONResponse({"error": "Spotify Premium is required for playback control."}, status_code=403)

    if play_res.status_code >= 400:
        print(f"[ERROR] Playback failed {play_res.status_code}: {play_res.text}")
        return JSONResponse({"error": f"Playback failed: {play_res.text}"}, status_code=500)

    return {"status": "playing"}


@app.get("/reveal")
def reveal():
    user = sessions.get("user")
    if not user or not user.get("current_track"):
        return JSONResponse({"error": "No track loaded"}, status_code=400)

    track = user["current_track"]
    images = track["album"].get("images", [])

    return {
        "name": track["name"],
        "artist": track["artists"][0]["name"],
        "year": track["album"]["release_date"][:4],
        "image": images[0]["url"] if images else None,
    }


@app.get("/logout")
def logout():
    sessions.clear()
    return RedirectResponse("/")
