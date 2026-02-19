import os
import random
import requests
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

app = FastAPI()
templates = Jinja2Templates(directory="templates")

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

PLAYLIST_ID = "https://open.spotify.com/playlist/6i2Qd6OpeRBAzxfscNXeWp?si=1VSjOCguSxuPeL1atDZaOA"

sessions = {}

def get_playlist_tracks():
    token = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET),
    ).json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}

    res = requests.get(
        f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100",
        headers=headers,
    )

    return [item["track"] for item in res.json()["items"]]


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state user-read-playback-state",
        "redirect_uri": REDIRECT_URI,
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
        auth=(CLIENT_ID, CLIENT_SECRET),
    ).json()

    access_token = token_res["access_token"]

    sessions["user"] = {
        "token": access_token,
        "current_song": None
    }

    return RedirectResponse("/")


@app.get("/play")
def play():
    user = sessions.get("user")
    if not user:
        return {"error": "Not logged in"}

    tracks = get_playlist_tracks()
    track = random.choice(tracks)

    user["current_song"] = track

    headers = {
        "Authorization": f"Bearer {user['token']}",
        "Content-Type": "application/json"
    }

    requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers=headers,
        json={"uris": [track["uri"]]}
    )

    return {"status": "Playing"}


@app.get("/reveal")
def reveal():
    user = sessions.get("user")
    if not user or not user["current_song"]:
        return {"error": "No song"}

    track = user["current_song"]

    return {
        "name": track["name"],
        "artist": track["artists"][0]["name"],
        "year": track["album"]["release_date"][:4],
        "album_image": track["album"]["images"][0]["url"]
    }

