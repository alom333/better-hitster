import os
import random
import requests
import base64
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ==============================
# ENV VARIABLES (Set in Render)
# ==============================
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

# IMPORTANT:
# Only the playlist ID, NOT full URL
PLAYLIST_ID = "6kA1H3sioFmZp03rmRF9t4"

# Simple in-memory session
sessions = {}


# ==============================
# Helper: Basic Auth Header
# ==============================
def get_basic_auth():
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    return base64.b64encode(auth_str.encode()).decode()


# ==============================
# Get Playlist Tracks (USER TOKEN)
# ==============================
def get_playlist_tracks(user_token):
    headers = {"Authorization": f"Bearer {user_token}"}
    tracks = []
    # Official Spotify API Endpoint
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100"

    while url:
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code != 200:
            print(f"❌ API Error {res.status_code}: {res.text}")
            break

        data = res.json()
        if "items" not in data:
            break

        for item in data["items"]:
            track = item.get("track")
            # Filter out local files or null tracks
            if track and track.get("uri") and not track.get("is_local"):
                tracks.append(track)

        url = data.get("next")  # Spotify provides this for pagination

    print(f"✅ Loaded {len(tracks)} tracks.")
    return tracks


# ==============================
# Homepage
# ==============================
@app.get("/")
def home(request: Request):
    logged_in = "user" in sessions
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "logged_in": logged_in}
    )


# ==============================
# Login
# ==============================
@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state user-read-playback-state",
        "redirect_uri": REDIRECT_URI,
        "show_dialog": True
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)


# ==============================
# Callback
# ==============================
@app.get("/callback")
def callback(code: str):
    token_res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={
            "Authorization": f"Basic {get_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=10
    )

    token_json = token_res.json()

    if "access_token" not in token_json:
        print("❌ Auth Error:", token_json)
        return {"error": "Authentication failed", "details": token_json}

    sessions["user"] = {
        "token": token_json["access_token"],
        "current_song": None,
        "buffer": []
    }

    return RedirectResponse("/")


# ==============================
# Play Random Song
# ==============================
@app.get("/play")
def play():
    user = sessions.get("user")
    if not user:
        return {"error": "Not logged in"}

    token = user["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Load playlist if empty
    if not user["buffer"]:
        user["buffer"] = get_playlist_tracks(token)

    if not user["buffer"]:
        return {"error": "Playlist is empty or could not be loaded."}

    song = random.choice(user["buffer"])
    user["current_song"] = song

    # Play song
    play_res = requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={"uris": [song["uri"]]},
        timeout=5
    )

    if play_res.status_code == 404:
        return {"error": "No active device. Open Spotify on your phone."}

    if play_res.status_code >= 400:
        print("❌ Play Error:", play_res.text)
        return {"error": "Playback failed."}

    return {"status": "Playing"}


# ==============================
# Reveal Song Info
# ==============================
@app.get("/reveal")
def reveal():
    user = sessions.get("user")

    if not user or not user["current_song"]:
        return {"error": "No song playing"}

    song = user["current_song"]

    return {
        "name": song["name"],
        "artist": song["artists"][0]["name"],
        "year": song["album"]["release_date"][:4],
        "image": song["album"]["images"][0]["url"]
    }


# ==============================
# Logout
# ==============================
@app.get("/logout")
def logout():
    sessions.clear()
    return RedirectResponse("/")


