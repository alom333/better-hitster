# main.py
import os
import time
import random
import requests
import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- config from env ---
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")  # e.g. https://your-app.onrender.com/callback
PLAYLIST_ID = os.getenv("PLAYLIST_ID")    # just the playlist id, e.g. 6kA1H3sioFmZp03rmRF9t4

if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI or not PLAYLIST_ID:
    print("WARNING: one of required env vars is missing. Set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, REDIRECT_URI, PLAYLIST_ID")

# in-memory storage (simple)
sessions = {}        # session_id -> { access_token, refresh_token, expires_at, buffer, current_song }
oauth_states = set() # temp set for state validation

# --- helpers -----------------------------------------------------------------

def basic_auth_header():
    import base64
    token = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/x-www-form-urlencoded"}

def exchange_code_for_token(code: str):
    """
    Authorization Code -> access_token + refresh_token
    """
    url = "https://accounts.spotify.com/api/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    res = requests.post(url, data=data, auth=(CLIENT_ID, CLIENT_SECRET), timeout=10)
    try:
        return res.json()
    except Exception as e:
        print("exchange_code_for_token - invalid json:", res.status_code, res.text)
        raise

def refresh_access_token(refresh_token: str):
    url = "https://accounts.spotify.com/api/token"
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    res = requests.post(url, data=data, auth=(CLIENT_ID, CLIENT_SECRET), timeout=10)
    try:
        return res.json()
    except Exception as e:
        print("refresh_access_token - invalid json:", res.status_code, res.text)
        raise

def ensure_valid_user_token(session_obj: dict) -> Optional[str]:
    """
    Ensures session_obj has a valid access token. Refreshes it if expired.
    Returns access token or None.
    """
    if not session_obj:
        return None
    if "access_token" not in session_obj:
        return None
    if session_obj.get("expires_at", 0) > time.time() + 5:
        return session_obj["access_token"]
    # expired or about to; try refresh
    refresh_token = session_obj.get("refresh_token")
    if not refresh_token:
        return None
    try:
        token_json = refresh_access_token(refresh_token)
    except Exception:
        return None
    if "access_token" in token_json:
        session_obj["access_token"] = token_json["access_token"]
        if "refresh_token" in token_json:
            session_obj["refresh_token"] = token_json["refresh_token"]
        expires_in = token_json.get("expires_in", 3600)
        session_obj["expires_at"] = time.time() + int(expires_in)
        return session_obj["access_token"]
    print("refresh failed:", token_json)
    return None

def get_client_credentials_token() -> Optional[str]:
    url = "https://accounts.spotify.com/api/token"
    data = {"grant_type": "client_credentials"}
    res = requests.post(url, data=data, auth=(CLIENT_ID, CLIENT_SECRET), timeout=10)
    try:
        js = res.json()
    except Exception:
        print("client credentials token error:", res.status_code, res.text)
        return None
    return js.get("access_token")

def fetch_playlist_tracks_using_token(token: str):
    """
    Fetch all tracks for PLAYLIST_ID using provided token.
    Returns list of track dicts (as returned by Spotify /tracks endpoint) or [].
    """
    if not token:
        return []
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 100}
    while url:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        try:
            data = res.json()
        except Exception:
            print("fetch_playlist_tracks_using_token - invalid json:", res.status_code, res.text)
            return []
        items = data.get("items", [])
        for item in items:
            t = item.get("track")
            if t and t.get("uri"):
                # skip local tracks that have no uri
                tracks.append({
                    "name": t.get("name"),
                    "uri": t.get("uri"),
                    "artists": t.get("artists"),
                    "album": t.get("album")
                })
        url = data.get("next")
        params = None
    return tracks

def get_playlist_tracks(fallback_user_token: Optional[str] = None):
    """
    Try server client credentials first (works for public playlists).
    If that fails or returns empty and fallback_user_token provided, try with user token (required for private playlists).
    """
    # try client credentials token
    client_token = get_client_credentials_token()
    if client_token:
        tlist = fetch_playlist_tracks_using_token(client_token)
        if tlist:
            print(f"Loaded {len(tlist)} tracks via client credentials")
            return tlist
    # fallback to user token
    if fallback_user_token:
        tlist = fetch_playlist_tracks_using_token(fallback_user_token)
        if tlist:
            print(f"Loaded {len(tlist)} tracks via user token fallback")
            return tlist
    print("No tracks loaded for playlist", PLAYLIST_ID)
    return []

def pick_random_track_from_buffer(session_obj):
    buf = session_obj.get("buffer") or []
    if not buf:
        return None
    # choose and remove (so next time it's not the same)
    idx = random.randrange(len(buf))
    track = buf.pop(idx)
    session_obj["buffer"] = buf
    return track

# --- routes ------------------------------------------------------------------

@app.get("/")
def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
def login():
    state = secrets.token_urlsafe(16)
    oauth_states.add(state)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user-modify-playback-state user-read-playback-state user-read-currently-playing",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "show_dialog": "true"
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/callback")
def callback(code: Optional[str] = None, state: Optional[str] = None):
    # spotify redirects with code and state
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)
    if not state or state not in oauth_states:
        print("callback: invalid or missing state:", state)
        return JSONResponse({"error": "invalid state"}, status_code=400)
    # remove used state
    oauth_states.discard(state)

    # exchange code for tokens
    try:
        token_json = exchange_code_for_token(code)
    except Exception as e:
        print("callback exchange error:", e)
        return JSONResponse({"error": "token exchange failed"}, status_code=500)

    if "access_token" not in token_json:
        # show Spotify's error to logs and return friendly message
        print("callback token response:", token_json)
        return JSONResponse({"error": "couldn't obtain access token", "details": token_json}, status_code=400)

    # create session
    session_id = secrets.token_urlsafe(24)
    access_token = token_json["access_token"]
    refresh_token = token_json.get("refresh_token")
    expires_in = int(token_json.get("expires_in", 3600))
    expires_at = time.time() + expires_in

    # fetch playlist tracks: try client credentials first; fallback to user token
    tracks = get_playlist_tracks(fallback_user_token=access_token)

    sessions[session_id] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "buffer": tracks.copy(),
        "current_song": None,
    }

    resp = RedirectResponse("/")
    # set session cookie (HttpOnly)
    resp.set_cookie("session_id", session_id, httponly=True, samesite="lax", max_age=60*60*24)
    return resp

@app.get("/me")
def me(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        return {"logged_in": False}
    session_obj = sessions[session_id]
    token = ensure_valid_user_token(session_obj)
    if not token:
        return {"logged_in": False}
    return {"logged_in": True}

@app.get("/logout")
def logout(request: Request):
    session_id = request.cookies.get("session_id")
    resp = RedirectResponse("/")
    if session_id and session_id in sessions:
        del sessions[session_id]
    # clear cookie
    resp.delete_cookie("session_id")
    return resp

@app.get("/play")
def play(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        return {"error": "Not logged in"}
    session_obj = sessions[session_id]
    token = ensure_valid_user_token(session_obj)
    if not token:
        return {"error": "Session expired. Please login again."}

    # ensure buffer has tracks, otherwise try to load again using user token
    if not session_obj.get("buffer"):
        session_obj["buffer"] = get_playlist_tracks(fallback_user_token=token)
    if not session_obj.get("buffer"):
        return {"error": "Playlist is empty or couldn't be loaded."}

    # pick next random track (and remove from buffer)
    track = pick_random_track_from_buffer(session_obj)
    if not track:
        return {"error": "Playlist buffer empty."}

    session_obj["current_song"] = track

    # ensure device exists
    headers = {"Authorization": f"Bearer {token}"}
    try:
        devices_res = requests.get("https://api.spotify.com/v1/me/player/devices", headers=headers, timeout=10)
        devices_json = devices_res.json()
    except Exception as e:
        print("devices fetch error:", e)
        return {"error": "Failed to fetch devices. Open Spotify on your phone and try again."}

    devices = devices_json.get("devices", [])
    if not devices:
        return {"error": "No active Spotify device found. Open the Spotify app (phone) and make it active."}

    # pick a device: prefer active device
    device_id = None
    for d in devices:
        if d.get("is_active"):
            device_id = d.get("id")
            break
    if not device_id:
        device_id = devices[0].get("id")

    # transfer playback (optional)
    try:
        requests.put("https://api.spotify.com/v1/me/player", headers=headers, json={"device_ids": [device_id], "play": False}, timeout=5)
    except Exception as e:
        print("transfer error:", e)

    # play the chosen track
    try:
        play_res = requests.put("https://api.spotify.com/v1/me/player/play", headers=headers, json={"uris": [track["uri"]]}, timeout=10)
        if play_res.status_code in (401, 403):
            return {"error": "Playback failed (auth). Please login again."}
        if play_res.status_code == 404:
            return {"error": "No active device (404). Open Spotify on a device."}
    except Exception as e:
        print("play error:", e)
        return {"error": "Failed to start playback. Make sure Spotify is open on your phone."}

    return {"status": "playing"}

@app.get("/reveal")
def reveal(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        return {"error": "Not logged in"}
    session_obj = sessions[session_id]
    track = session_obj.get("current_song")
    if not track:
        return {"error": "No song playing"}
    album = track.get("album", {})
    image_url = None
    imgs = album.get("images") or []
    if imgs:
        image_url = imgs[0].get("url")
    year = ""
    release_date = album.get("release_date", "")
    if release_date:
        year = release_date.split("-")[0]
    return {"name": track.get("name"), "artist": track.get("artists", [{}])[0].get("name"), "year": year, "image": image_url}
