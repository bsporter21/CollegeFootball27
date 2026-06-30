import asyncio
import json
import os
import sys
from pathlib import Path
import aiohttp
import cv2
import base64
import tempfile
import mimetypes

# ── Frame Extraction Helpers ───────────────────────────────────────────────────

async def download_file(session, url):
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
            temp = tempfile.NamedTemporaryFile(delete=False)
            temp.write(data)
            temp.close()
            return temp.name
    except:
        return None

def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def extract_frames(video_path, num_frames=12):
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if total_frames == 0:
        return []

    step = max(1, total_frames // num_frames)
    frames_b64 = []

    for i in range(0, total_frames, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        success, frame = cap.read()
        if not success:
            continue

        _, buffer = cv2.imencode(".jpg", frame)
        frames_b64.append(base64.b64encode(buffer).decode("utf-8"))

        if len(frames_b64) >= num_frames:
            break

    cap.release()
    return frames_b64

# ── Configuration ──────────────────────────────────────────────────────────────
GAMERTAGS = ["xxxOpie24xxx", "Pope115", "THEE WATERB0Y", "TGoob24", "trost24"]
GAME_FILTER = ("college football 27", "college football 26")
STATE_FILE = Path(__file__).parent / "state.json"
SHEET_URL = "https://script.google.com/macros/s/AKfycbz_JYmSKRj8w8GQEztu59ygyxsZ8lL3Y-VktNFXsuMLPCHRTVleo6WrA3Fry_cfd0OU-w/exec"

# ── State Management ───────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return {
                "xuids": data.get("xuids", {}),
                "processed": set(data.get("processed", [])),
            }
        except (json.JSONDecodeError, ValueError):
            pass
    return {"xuids": {}, "processed": set()}

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps({
        "xuids": state["xuids"],
        "processed": sorted(state["processed"]),
    }, indent=2))

# ── API Helpers ────────────────────────────────────────────────────────────────
def get_api_key() -> str:
    key = os.environ.get("XBL_API_KEY")
    if not key:
        sys.exit("ERROR: XBL_API_KEY is not set.")
    return key

def xbl_headers(api_key: str) -> dict:
    return {"X-Authorization": api_key, "Accept-Language": "en-US", "Accept": "application/json"}

def matches_game(title_name: str) -> bool:
    return any(f in title_name.lower() for f in GAME_FILTER)

async def resolve_xuid(session: aiohttp.ClientSession, api_key: str, gamertag: str) -> str | None:
    url = f"https://xbl.io/api/v2/friends/search?gt={aiohttp.helpers.quote(gamertag)}"
    try:
        async with session.get(url, headers=xbl_headers(api_key)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            profile_users = data.get("content", {}).get("profileUsers", [])
            if not profile_users:
                people = data.get("people", [])
                if people:
                    return people[0].get("xuid")
                return None
            return profile_users[0].get("id")
    except aiohttp.ClientError:
        return None

async def get_xuid(session: aiohttp.ClientSession, api_key: str, gamertag: str, state: dict) -> str | None:
    cached = state["xuids"].get(gamertag)
    if cached: return cached
    xuid = await resolve_xuid(session, api_key, gamertag)
    if xuid:
        state["xuids"][gamertag] = xuid
    return xuid

async def fetch_clips(session: aiohttp.ClientSession, api_key: str, xuid: str) -> list[dict]:
    url = f"https://xbl.io/api/v2/dvr/gameclips/{xuid}"
    async with session.get(url, headers=xbl_headers(api_key)) as resp:
        if resp.status != 200: return []
        data = await resp.json()
        return data.get("content", {}).get("gameClips", [])

async def fetch_screenshots(session: aiohttp.ClientSession, api_key: str, xuid: str) -> list[dict]:
    url = f"https://xbl.io/api/v2/dvr/screenshots/{xuid}"
    async with session.get(url, headers=xbl_headers(api_key)) as resp:
        if resp.status != 200: return []
        data = await resp.json()
        return data.get("content", {}).get("screenshots", [])

# ── Sheet Push Logic ───────────────────────────────────────────────────────────
async def push_to_sheets(session: aiohttp.ClientSession, timestamp: str, media_url: str) -> bool:
    # Download the media file locally
    file_path = await download_file(session, media_url)
    if not file_path:
        return False

    # Detect file type
    mime_type, _ = mimetypes.guess_type(media_url)

    # Screenshot → single frame
    if mime_type and mime_type.startswith("image"):
        frames = [encode_image(file_path)]
    else:
        # Video → extract 12 frames for maximum immersion
        frames = extract_frames(file_path, num_frames=12)

    # Build payload for Apps Script
    payload = {
        "timestamp": timestamp,
        "media_url": media_url,
        "frames": frames
    }

    # Send to Google Apps Script
    try:
        async with session.post(SHEET_URL, json=payload) as resp:
            return resp.status == 200
    except aiohttp.ClientError:
        return False

# ── Processing ─────────────────────────────────────────────────────────────────
async def scan_gamertag(session: aiohttp.ClientSession, api_key: str, gamertag: str, state: dict) -> set:
    new_ids: set[str] = set()
    xuid = await get_xuid(session, api_key, gamertag, state)
    if not xuid: return new_ids

    clips, screenshots = await asyncio.gather(
        fetch_clips(session, api_key, xuid),
        fetch_screenshots(session, api_key, xuid),
    )

    cf_clips = [c for c in clips if matches_game(c.get("titleName", ""))]
    cf_shots = [s for s in screenshots if matches_game(s.get("titleName", ""))]

    for clip in cf_clips:
        clip_id = clip.get("gameClipId", "")
        if clip_id in state["processed"] or clip_id in new_ids: continue
        
        # Get mp4 URL
        mp4_url = ""
        for u in clip.get("gameClipUris", []):
            if u.get("fileSize", 0) > 0:
                mp4_url = u.get("uri", "")
                break
        
        if await push_to_sheets(session, clip.get("dateRecorded", ""), mp4_url):
            new_ids.add(clip_id)
        await asyncio.sleep(1)

    for shot in cf_shots:
        shot_id = shot.get("screenshotId", "")
        if shot_id in state["processed"] or shot_id in new_ids: continue
        
        # Get image URL
        uris = shot.get("screenshotUris", [])
        img_url = uris[0].get("uri", "") if uris else ""
        
        if await push_to_sheets(session, shot.get("dateTaken", ""), img_url):
            new_ids.add(shot_id)
        await asyncio.sleep(1)

    return new_ids

async def run() -> None:
    api_key = get_api_key()
    state = load_state()
    all_new: set[str] = set()
    connector = aiohttp.TCPConnector(limit=10)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        for gamertag in GAMERTAGS:
            new_ids = await scan_gamertag(session, api_key, gamertag, state)
            all_new |= new_ids
            await asyncio.sleep(1)

    state["processed"] |= all_new
    save_state(state)
    print(f"[DONE] Queued {len(all_new)} new items to Sheets.")

if __name__ == "__main__":
    asyncio.run(run())
