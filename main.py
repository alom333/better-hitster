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
    # 1. Get the Token
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    token_url = "https://accounts.spotify.com/api/token"
    
    token_res = requests.post(
        token_url,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {auth_header}"}
    )
    
    token_data = token_res.json()
    token = token_data.get("access_token")

    if not token:
        print(f"❌ Failed to get track-fetching token: {token_data}")
        return []

    # 2. Get the Tracks
    # Note: Using the official API URL directly
    tracks_url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=50"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        res = requests.get(tracks_url, headers=headers)
        data = res.json()
        
        if "items" not in data:
            print(f"❌ API response missing 'items': {data}")
            return []

        # Extracting valid tracks only
        valid_tracks = [
            item["track"] for item in data["items"] 
            if item.get("track") and item["track"].get("uri")
        ]
        
        print(f"✅ Successfully loaded {len(valid_tracks)} tracks!")
        return valid_tracks

    except Exception as e:
        print(f"❌ Error during track fetch: {e}")
        return []

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
    if not user:
        return {"error": "Not logged in"}

    # Use a try-except block so the "loading" state in JS doesn't stay forever
    try:
        headers = {"Authorization": f"Bearer {user['token']}"}
        
        # 1. Check for tracks
        if not user.get("buffer"):
            user["buffer"] = get_playlist_tracks()
        
        if not user["buffer"]:
            return {"error": "Playlist is empty or couldn't be loaded."}

        song = random.choice(user["buffer"])
        user["current_song"] = song

        # 2. Play the song (with a short timeout)
        play_res = requests.put(
            "https://api.spotify.com/v1/me/player/play",
            headers=headers,
            json={"uris": [song["uri"]]},
            timeout=5 
        )

        if play_res.status_code == 404:
            return {"error": "No active device. Open Spotify on your phone!"}
            
        return {"status": "Playing"}

    except Exception as e:
        print(f"Play error: {e}")
        return {"error": "Server error while trying to play."}

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


