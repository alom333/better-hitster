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

PLAYLIST_ID = "1puQ0hv40TUre24cFillJS"

sessions = {}


def get_basic_auth():
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    return base64.b64encode(auth_str.encode()).decode()


def get_playlist_tracks(user_token):
    headers = {"Authorization": f"Bearer {user_token}"}
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100"
    print(f"--- Attempting to load playlist: {PLAYLIST_ID} ---")

    while url:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"❌ Spotify API Error {res.status_code}: {res.text}")
            return []
        data = res.json()
        for item in data.get("items", []):
            track = item.get("track")
            if track and track.get("uri"):
                tracks.append(track)
        url = data.get("next")

    print(f"✅ Loaded {len(tracks)} tracks." if tracks else "⚠️ 0 tracks found.")
    return tracks


@app.get("/")
def home(request: Request):
    logged_in = "user" in sessions
    return templates.TemplateResponse("index.html", {"request": request, "logged_in": logged_in})


@app.get("/me")
def me():
    return {"logged_in": "user" in sessions}


@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state user-read-playback-state playlist-read-private playlist-read-collaborative",
        "redirect_uri": REDIRECT_URI,
        "show_dialog": True
    }
    return RedirectResponse("https://accounts.spotify.com/authorize?" + urlencode(params))


@app.get("/callback")
def callback(code: str):
    token_res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        headers={"Authorization": f"Basic {get_basic_auth()}", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=10
    )
    token_json = token_res.json()
    if "access_token" not in token_json:
        print("❌ Auth Error:", token_json)
        return JSONResponse({"error": "Authentication failed", "details": token_json}, status_code=400)

    sessions["user"] = {
        "token": token_json["access_token"],
        "refresh_token": token_json.get("refresh_token"),
        "current_song": None,
        "buffer": []
    }
    return RedirectResponse("/")


@app.get("/play")
def play():
    user = sessions.get("user")
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    token = user["token"]

    if not user["buffer"]:
        user["buffer"] = get_playlist_tracks(token)

    if not user["buffer"]:
        return JSONResponse({
            "error": "Could not load playlist. Make sure your Spotify account email is added in the Developer Dashboard under User Management."
        }, status_code=500)

    song = random.choice(user["buffer"])
    user["current_song"] = song

    play_res = requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"uris": [song["uri"]]},
        timeout=5
    )

    if play_res.status_code == 404:
        return JSONResponse({"error": "No active Spotify device found. Open Spotify on your phone or desktop first."}, status_code=404)
    if play_res.status_code >= 400:
        print("❌ Play Error:", play_res.text)
        return JSONResponse({"error": "Playback failed.", "details": play_res.text}, status_code=500)

    return {"status": "Playing"}


@app.get("/reveal")
def reveal():
    user = sessions.get("user")
    if not user or not user["current_song"]:
        return JSONResponse({"error": "No song playing"}, status_code=400)

    song = user["current_song"]
    return {
        "name": song["name"],
        "artist": song["artists"][0]["name"],
        "year": song["album"]["release_date"][:4],
        "image": song["album"]["images"][0]["url"]
    }


@app.get("/debug")
def debug():
    user = sessions.get("user")
    if not user:
        return {"error": "Not logged in"}
    token = user["token"]
    me_res = requests.get("https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {token}"})
    playlist_res = requests.get(f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}", headers={"Authorization": f"Bearer {token}"})
    return {
        "me_status": me_res.status_code,
        "me_email": me_res.json().get("email"),
        "playlist_status": playlist_res.status_code,
        "playlist_name": playlist_res.json().get("name"),
        "playlist_error": playlist_res.json().get("error")
    }


@app.get("/logout")
def logout():
    sessions.clear()
    return RedirectResponse("/")
