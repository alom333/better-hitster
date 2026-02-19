import os
import random
import requests
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

app = FastAPI()
templates = Jinja2Templates(directory="templates")

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

PLAYLIST_ID = "6i2Qd6OpeRBAzxfscNXeWp"

sessions = {}

# -----------------------------
# Helper: Get playlist tracks
# -----------------------------
def get_playlist_tracks():
    token = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET),
    ).json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}

    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100"

    while url:
        res = requests.get(url, headers=headers).json()
        for item in res["items"]:
            if item["track"]:
                tracks.append(item["track"])
        url = res.get("next")

    return tracks


# -----------------------------
# Homepage
# -----------------------------
@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# -----------------------------
# Login
# -----------------------------
@app.get("/login")
def login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state user-read-playback-state user-read-currently-playing",
        "redirect_uri": REDIRECT_URI,
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)


# -----------------------------
# Callback
# -----------------------------
# @app.get("/callback")
# def callback(code: str):
    # token_res = requests.post(
    #     "https://accounts.spotify.com/api/token",
    #     data={
    #         "grant_type": "authorization_code",
    #         "code": code,
    #         "redirect_uri": REDIRECT_URI,
    #     },
    #     auth=(CLIENT_ID, CLIENT_SECRET),
    # ).json()

    # sessions["user"] = {
    #     "token": token_res["access_token"],
    #     "current_song": None,
    #     "buffer": get_playlist_tracks()
    # }

    # return RedirectResponse("/")

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
    )

    token_json = token_res.json()

    print("STATUS:", token_res.status_code)
    print("TEXT:", token_res.text)

    if "access_token" not in token_json:
        return {"error": token_json}

    sessions["user"] = {
        "token": token_json["access_token"],
        "current_song": None,
        "buffer": get_playlist_tracks()
    }

    return RedirectResponse("/")



# -----------------------------
# Play Song
# -----------------------------
@app.get("/play")
def play():
    user = sessions.get("user")
    if not user:
        return {"error": "Not logged in"}

    token = user["token"]

    # Get devices
    devices = requests.get(
        "https://api.spotify.com/v1/me/player/devices",
        headers={"Authorization": f"Bearer {token}"}
    ).json()

    if not devices["devices"]:
        return {"error": "Open Spotify on your phone first."}

    device_id = devices["devices"][0]["id"]

    # Transfer playback
    requests.put(
        "https://api.spotify.com/v1/me/player",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_ids": [device_id], "play": False}
    )

    # Pick random song
    if not user["buffer"]:
        user["buffer"] = get_playlist_tracks()

    song = random.choice(user["buffer"])
    user["current_song"] = song

    # Play it
    requests.put(
        "https://api.spotify.com/v1/me/player/play",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={
            "uris": [song["uri"]],
            "position_ms": 0
        }
    )

    return {"status": "Playing"}


# -----------------------------
# Reveal
# -----------------------------
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



