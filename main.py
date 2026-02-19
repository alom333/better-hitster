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
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ ERROR: CLIENT_ID or CLIENT_SECRET is None. Check Render Env Vars!")
        return []

    # 1. Get the Token
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    
    try:
        token_res = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth_header}"},
            timeout=10
        )
        token = token_res.json().get("access_token")
        
        if not token:
            print(f"❌ Token Error: {token_res.text}")
            return []

        # 2. Get the Tracks - Note the corrected URL structure
        # We use the direct tracks endpoint
        tracks_url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks"
        params = {"limit": 50, "fields": "items(track(name,uri,artists,album(name,release_date,images)))"}
        
        res = requests.get(
            tracks_url, 
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10
        )
        
        data = res.json()
        if "items" not in data:
            print(f"❌ Playlist API Error: {data}")
            return []

        tracks = [i["track"] for i in data["items"] if i.get("track") and i["track"].get("uri")]
        print(f"✅ Success! Loaded {len(tracks)} tracks.")
        return tracks

    except Exception as e:
        print(f"❌ Fatal Error in get_playlist_tracks: {e}")
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



