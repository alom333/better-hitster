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

# Make sure these match your Spotify Developer Dashboard exactly
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI") # e.g., http://localhost:8000/callback

PLAYLIST_ID = "6kA1H3sioFmZp03rmRF9t4"

# Note: Using a global dict means only ONE person can use this app at a time.
session = {}

# ==============================
# HOME
# ==============================
@app.get("/")
def home(request: Request):
    logged_in = "token" in session
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "logged_in": logged_in}
    )

# ==============================
# LOGIN - Fixed Redirect URL
# ==============================
@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state",
        "redirect_uri": REDIRECT_URI,
        "show_dialog": True # Useful for debugging login issues
    }
    # Corrected Spotify Authorize URL
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

# ==============================
# CALLBACK - Fixed Token URL
# ==============================
@app.get("/callback")
def callback(code: str):
    auth_header = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    ).decode()

    # Corrected Spotify Token URL
    token_res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    token_json = token_res.json()

    if "access_token" not in token_json:
        print("AUTH ERROR:", token_json)
        return {"error": "Authentication failed", "details": token_json}

    session["token"] = token_json["access_token"]
    session["current_song"] = None

    return RedirectResponse("/")

# ==============================
# LOAD PLAYLIST - Fixed API URL
# ==============================
def load_playlist(token):
    headers = {"Authorization": f"Bearer {token}"}
    
    # Corrected Playlist Tracks URL
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=50"

    res = requests.get(url, headers=headers)
    
    if res.status_code != 200:
        print("PLAYLIST ERROR:", res.text)
        return []

    data = res.json()
    tracks = []

    for item in data.get("items", []):
        track = item.get("track")
        if track and track.get("uri"):
            tracks.append(track)

    return tracks

# ==============================
# PLAY - Fixed API URL
# ==============================
@app.get("/play")
def play():
    if "token" not in session:
        return {"error": "Not logged in"}

    token = session["token"]
    tracks = load_playlist(token)

    if not tracks:
        return {"error": "Playlist empty or not accessible."}

    song = random.choice(tracks)
    session["current_song"] = song

    # Corrected Playback URL
    play_res = requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={"uris": [song["uri"]]}
    )

    # 404 usually means no active device is found
    if play_res.status_code == 404:
        return {"error": "No active device found. Open Spotify on your phone/PC first."}

    if play_res.status_code >= 400:
        print("PLAY ERROR:", play_res.text)
        return {"error": f"Playback failed: {play_res.status_code}"}

    return {"status": "Playing"}

# ==============================
# REVEAL / LOGOUT (Unchanged logic)
# ==============================
@app.get("/reveal")
def reveal():
    if not session.get("current_song"):
        return {"error": "No song playing"}
    
    song = session["current_song"]
    return {
        "name": song["name"],
        "artist": song["artists"][0]["name"],
        "year": song["album"]["release_date"][:4],
        "image": song["album"]["images"][0]["url"]
    }

@app.get("/logout")
def logout():
    session.clear()
    return RedirectResponse("/")
