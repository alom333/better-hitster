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

PLAYLIST_ID = "6kA1H3sioFmZp03rmRF9t4"
sessions = {}

def get_auth_header():
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    return base64.b64encode(auth_str.encode()).decode()

def get_playlist_tracks(token):
    if not token:
        print("❌ No token provided to get_playlist_tracks")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    tracks_url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks"
    params = {
        "limit": 50,
        "fields": "items(track(name,uri,artists,album(name,release_date,images)))"
    }

    try:
        res = requests.get(tracks_url, headers=headers, params=params, timeout=10)
        print(f"🔍 Playlist API status: {res.status_code}")

        if res.status_code != 200:
            print(f"❌ Playlist API Error: {res.status_code} - {res.text}")
            return []

        data = res.json()
        tracks = [
            item["track"] for item in data.get("items", [])
            if item.get("track") and item["track"].get("uri")
        ]

        print(f"✅ Loaded {len(tracks)} tracks.")
        return tracks

    except Exception as e:
        print(f"❌ Fatal Error in get_playlist_tracks: {e}")
        return []

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
def login():
    scopes = [
        "user-modify-playback-state",
        "user-read-playback-state",
        "user-read-currently-playing",
        "playlist-read-private",
        "playlist-read-collaborative"
    ]
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": " ".join(scopes),
        "redirect_uri": REDIRECT_URI,
        "show_dialog": True
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/callback")
def callback(code: str):
    token_res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Authorization": f"Basic {get_auth_header()}"},
    )

    token_json = token_res.json()
    access_token = token_json.get("access_token")

    if not access_token:
        print(f"AUTH ERROR: {token_json}")
        return {"error": "Auth failed", "details": token_json}

    tracks = get_playlist_tracks(access_token)

    sessions["user"] = {
        "token": access_token,
        "current_song": None,
        "buffer": tracks
    }

    return RedirectResponse(url="/")

@app.get("/status")
def status():
    user = sessions.get("user")
    if not user:
        return {"logged_in": False}
    return {"logged_in": True, "track_count": len(user.get("buffer", []))}

@app.get("/play")
def play():
    user = sessions.get("user")
    if not user:
        return {"error": "NOT_LOGGED_IN"}

    headers = {"Authorization": f"Bearer {user['token']}"}

    try:
        if not user.get("buffer"):
            print("🔄 Buffer empty, refilling...")
            user["buffer"] = get_playlist_tracks(user["token"])

        if not user["buffer"]:
            return {"error": "PLAYLIST_EMPTY"}

        devices_res = requests.get("https://api.spotify.com/v1/me/player/devices", headers=headers).json()
        devices = devices_res.get("devices", [])

        if not devices:
            return {"error": "No active Spotify device. Open Spotify on your phone or computer first!"}

        active_list = [d for d in devices if d['is_active']]
        device_id = active_list[0]['id'] if active_list else devices[0]['id']

        song = random.choice(user["buffer"])
        user["current_song"] = song

        play_url = f"https://api.spotify.com/v1/me/player/play?device_id={device_id}"
        play_payload = {
            "uris": [song["uri"]],
            "position_ms": random.randint(30000, 70000)
        }

        play_res = requests.put(play_url, headers=headers, json=play_payload, timeout=5)
        print(f"▶️ Play response: {play_res.status_code}")

        return {"status": "Playing"}

    except Exception as e:
        print(f"Play error: {e}")
        return {"error": f"Server error: {e}"}

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
