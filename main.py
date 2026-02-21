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
    # FIXED: Ensured the URL path is exactly correct
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100"
    headers = {"Authorization": f"Bearer {token}"}
    
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        return []
    
    data = res.json()
    return [item["track"] for item in data.get("items", []) if item.get("track") and item["track"].get("uri")]

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
def login():
    scopes = [
        "user-modify-playback-state",
        "user-read-playback-state",
        "playlist-read-private"
    ]
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": " ".join(scopes),
        "redirect_uri": REDIRECT_URI,
        "show_dialog": "true"
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/callback")
def callback(code: str):
    token_url = "https://accounts.spotify.com/api/token"
    res = requests.post(
        token_url,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        headers={"Authorization": f"Basic {get_auth_header()}"},
    )
    
    token_json = res.json()
    access_token = token_json.get("access_token")
    if not access_token: return {"error": "Auth failed"}

    sessions["user"] = {
        "token": access_token,
        "current_song": None,
        "buffer": get_playlist_tracks(access_token)
    }
    return RedirectResponse(url="/")

@app.get("/play")
def play():
    user = sessions.get("user")
    if not user: return {"error": "Not logged in"}
    
    headers = {"Authorization": f"Bearer {user['token']}"}
    
    # 1. Get Devices
    dev_res = requests.get("https://api.spotify.com/v1/me/player/devices", headers=headers).json()
    devices = dev_res.get("devices", [])
    if not devices:
        return {"error": "No active Spotify device found."}

    # Pick a device (prefer active, else first available)
    active_devs = [d for d in devices if d['is_active']]
    device_id = active_devs[0]['id'] if active_devs else devices[0]['id']

    # 2. Select the random song
    if not user.get("buffer"):
        user["buffer"] = get_playlist_tracks(user["token"])
    
    song = random.choice(user["buffer"])
    user["current_song"] = song

    # 3. PLAY the song directly on that device
    # We pass the device_id as a query parameter to the /play endpoint
    # This is much more reliable than trying to 'wake up' the player separately
    play_url = f"https://api.spotify.com/v1/me/player/play?device_id={device_id}"
    
    play_res = requests.put(
        play_url,
        headers=headers,
        json={"uris": [song["uri"]]}
    )

    if play_res.status_code > 204:
        return {"error": "Playback failed", "details": play_res.text}
    
    return {"status": "Playing", "song": song["name"]}

@app.get("/reveal")
def reveal():
    user = sessions.get("user")
    if not user or not user["current_song"]:
        return {"error": "No song playing"}
    
    s = user["current_song"]
    return {
        "name": s["name"],
        "artist": s["artists"][0]["name"],
        "year": s["album"]["release_date"][:4],
        "image": s["album"]["images"][0]["url"]
    }
