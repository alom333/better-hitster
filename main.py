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
# Ensure there are no spaces in your ID
PLAYLIST_ID = "6kA1H3sioFmZp03rmRF9t4"

sessions = {}

def get_auth_header():
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    return base64.b64encode(auth_str.encode()).decode()

def get_playlist_tracks(token):
    # Using the absolute official Spotify API URL
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100"
    headers = {"Authorization": f"Bearer {token}"}
    
    print(f"--- Attempting to fetch tracks for playlist {PLAYLIST_ID} ---")
    res = requests.get(url, headers=headers)
    
    if res.status_code != 200:
        print(f"❌ ERROR {res.status_code}: {res.text}")
        return []
    
    data = res.json()
    tracks = []
    for item in data.get("items", []):
        t = item.get("track")
        if t and t.get("uri"):
            tracks.append(t)
            
    print(f"✅ Success! Loaded {len(tracks)} tracks.")
    return tracks

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
def login():
    # Adding more scopes to ensure permission
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
        "show_dialog": "true"
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/callback")
def callback(code: str):
    # Exchange code for token
    token_url = "https://accounts.spotify.com/api/token"
    auth_header = get_auth_header()
    
    res = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Authorization": f"Basic {auth_header}"},
    )
    
    token_json = res.json()
    access_token = token_json.get("access_token")
    
    if not access_token:
        return {"error": "Auth failed", "details": token_json}

    # Fetch tracks IMMEDIATELY using the new user token
    tracks = get_playlist_tracks(access_token)
    
    sessions["user"] = {
        "token": access_token,
        "current_song": None,
        "buffer": tracks
    }
    
    return RedirectResponse(url="/")

@app.get("/play")
def play():
    user = sessions.get("user")
    if not user: return {"error": "Not logged in"}
    headers = {"Authorization": f"Bearer {user['token']}"}
    
    # 1. Get all available devices
    devices_res = requests.get("https://api.spotify.com/v1/me/player/devices", headers=headers).json()
    devices = devices_res.get("devices", [])
    if not devices:
        return {"error": "Open Spotify on your phone first!"}

    # 2. Pick a device (prefer active, else take the first one)
    active = [d for d in devices if d['is_active']]
    device_id = active[0]['id'] if active else devices[0]['id']

    # 3. FORCE WAKE UP the device (This stops the need to play a song manually)
    requests.put(
        "https://api.spotify.com/v1/me/player",
        headers=headers,
        json={"device_ids": [device_id], "play": True} # 'play': True wakes it up
    )

    # 4. Pick and Play the song
    if not user.get("buffer"):
        user["buffer"] = get_playlist_tracks(user["token"])
    
    song = random.choice(user["buffer"])
    user["current_song"] = song

    requests.put(
        f"https://api.spotify.com/v1/me/player/play?device_id={device_id}",
        headers=headers,
        json={"uris": [song["uri"]]}
    )
    
    return {"status": "Playing"}

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
