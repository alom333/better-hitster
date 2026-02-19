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

# Render pulls these from your Dashboard Environment settings
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI") 

PLAYLIST_ID = "6i2Qd6OpeRBAzxfscNXeWp"
sessions = {}

def get_auth_header():
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    return base64.b64encode(auth_str.encode()).decode()

def get_playlist_tracks():
    # Client Credentials Flow for the track list
    res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {get_auth_header()}"},
    )
    token = res.json().get("access_token")
    
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100"
    while url:
        res = requests.get(url, headers={"Authorization": f"Bearer {token}"}).json()
        if "items" not in res: break
        for item in res["items"]:
            if item.get("track"):
                tracks.append(item["track"])
        url = res.get("next")
    return tracks

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state user-read-playback-state user-read-currently-playing",
        "redirect_uri": REDIRECT_URI,
        "show_dialog": True # Useful for debugging
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/callback")
def callback(code: str):
    # Exchange code for Access Token
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
    
    # If this fails, it will print in your Render logs
    if "access_token" not in token_json:
        print(f"AUTH ERROR: {token_json}")
        return {"error": "Auth failed", "details": token_json}

    sessions["user"] = {
        "token": token_json["access_token"],
        "current_song": None,
        "buffer": get_playlist_tracks()
    }

    return RedirectResponse(url="/")

@app.get("/play")
def play():
    user = sessions.get("user")
    if not user: return {"error": "Not logged in"}

    headers = {"Authorization": f"Bearer {user['token']}"}
    
    # Check for active devices
    devices = requests.get("https://api.spotify.com/v1/me/player/devices", headers=headers).json()
    if not devices.get("devices"):
        return {"error": "No active Spotify device found. Open Spotify!"}

    # Pick random song
    if not user["buffer"]: user["buffer"] = get_playlist_tracks()
    song = random.choice(user["buffer"])
    user["current_song"] = song

    # Play
    requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers=headers,
        json={"uris": [song["uri"]]}
    )
    return {"status": "Playing"}

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
