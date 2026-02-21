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

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

# PUT ONLY THE PLAYLIST ID HERE (NOT FULL LINK)
PLAYLIST_ID = "6kA1H3sioFmZp03rmRF9t4"

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
# LOGIN
# ==============================
@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state",
        "redirect_uri": REDIRECT_URI,
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

# ==============================
# CALLBACK
# ==============================
@app.get("/callback")
def callback(code: str):

    auth_header = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    ).decode()

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
        return {"error": token_json}

    session["token"] = token_json["access_token"]
    session["current_song"] = None

    return RedirectResponse("/")

# ==============================
# LOAD PLAYLIST (USER TOKEN!)
# ==============================
def load_playlist(token):

    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=50"

    res = requests.get(url, headers=headers)
    data = res.json()

    if "items" not in data:
        print("PLAYLIST ERROR:", data)
        return []

    tracks = []

    for item in data["items"]:
        track = item["track"]
        if track and track.get("uri"):
            tracks.append(track)

    print("Loaded tracks:", len(tracks))
    return tracks

# ==============================
# PLAY
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

    play_res = requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={"uris": [song["uri"]]}
    )

    if play_res.status_code == 404:
        return {"error": "Open Spotify app on your phone first."}

    if play_res.status_code >= 400:
        print("PLAY ERROR:", play_res.text)
        return {"error": "Playback failed."}

    return {"status": "Playing"}

# ==============================
# REVEAL
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

# ==============================
# LOGOUT
# ==============================
@app.get("/logout")
def logout():
    session.clear()
    return RedirectResponse("/")
