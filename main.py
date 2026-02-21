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

PLAYLIST_ID = "6kA1H3sioFmZp03rmRF9t4"

sessions = {}


def get_basic_auth():
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    return base64.b64encode(auth_str.encode()).decode()


def refresh_access_token(refresh_token):
    res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={
            "Authorization": f"Basic {get_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=10
    )
    data = res.json()
    return data.get("access_token")


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

    if not tracks:
        print("⚠️ Request succeeded, but 0 tracks were found.")
    else:
        print(f"✅ Successfully loaded {len(tracks)} tracks.")

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
# Auth status (fixes /me 404)
# ==============================
@app.get("/me")
def me():
    logged_in = "user" in sessions
    return {"logged_in": logged_in}


# ==============================
# Login — ADD playlist-read-private + playlist-read-collaborative
# ==============================
@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": " ".join([
            "user-modify-playback-state",
            "user-read-playback-state",
            "playlist-read-private",         # ← REQUIRED for private playlists
            "playlist-read-collaborative"    # ← good to have
        ]),
        "redirect_uri": REDIRECT_URI,
        "show_dialog": True
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)


# ==============================
# Callback — also store refresh_token
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
        "refresh_token": token_json.get("refresh_token"),  # ← store refresh token
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
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    token = user["token"]

    # Load playlist if empty
    if not user["buffer"]:
        user["buffer"] = get_playlist_tracks(token)

        # If still empty, try refreshing the token once
        if not user["buffer"] and user.get("refresh_token"):
            print("🔄 Trying token refresh...")
            new_token = refresh_access_token(user["refresh_token"])
            if new_token:
                user["token"] = new_token
                token = new_token
                user["buffer"] = get_playlist_tracks(token)

    if not user["buffer"]:
        return JSONResponse({"error": "Playlist is empty or could not be loaded."}, status_code=500)

    song = random.choice(user["buffer"])
    user["current_song"] = song

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
        return JSONResponse({"error": "No active device. Open Spotify on your phone or desktop first."}, status_code=404)

    if play_res.status_code >= 400:
        print("❌ Play Error:", play_res.text)
        return JSONResponse({"error": "Playback failed.", "details": play_res.text}, status_code=500)

    return {"status": "Playing"}


# ==============================
# Reveal Song Info
# ==============================
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


# ==============================
# Logout
# ==============================
@app.get("/logout")
def logout():
    sessions.clear()
    return RedirectResponse("/")
