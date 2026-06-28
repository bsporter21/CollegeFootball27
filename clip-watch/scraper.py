"""
Clip Watch — EA SPORTS College Football 27 Xbox media tracker.

Watches a list of Xbox gamertags via the OpenXBL API. Each run, it pulls any
new game clips and screenshots for College Football 27 and posts them to a
Discord channel via webhook. Already-seen items are remembered in state.json
so nothing double-posts.

This script runs ONCE and exits. Scheduling is handled by GitHub Actions cron
(see .github/workflows/clip-watch.yml) — there is no internal loop.

Required environment variables (set as GitHub repo secrets):
  XBL_API_KEY          your OpenXBL key from https://xbl.io
  DISCORD_WEBHOOK_URL  the Discord channel webhook to post into
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp

# ── Configuration ──────────────────────────────────────────────────────────────

GAMERTAGS = [
    "xxxOpie24xxx",
    "Pope115",
    "THEE WATERB0Y",
    "TGoob24",
    "trost24",
]

# Case-insensitive substring match against titleName returned by the API.
# Matches "EA SPORTS™ College Football 27" and minor naming variations.
GAME_FILTER = "college football 27"

STATE_FILE = Path(__file__).parent / "state.json"

COLOR_GREEN = 0x57F287   # clips
COLOR_BLUE  = 0x5865F2   # screenshots


# ── State (XUID cache + processed IDs, persisted in one file) ───────────────────

def load_state() -> dict:
    """state.json shape: {"xuids": {gamertag: xuid}, "processed": [ids...]}"""
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
    STATE_FILE.write_text(
        json.dumps(
            {
                "xuids": state["xuids"],
                "processed": sorted(state["processed"]),
            },
            indent=2,
        )
    )


# ── Config from environment ────────────────────────────────────────────────────

def get_api_key() -> str:
    key = os.environ.get("XBL_API_KEY")
    if not key:
        sys.exit("ERROR: XBL_API_KEY is not set. Add it as a GitHub repo secret.")
    return key


def get_webhook_url() -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        sys.exit("ERROR: DISCORD_WEBHOOK_URL is not set. Add it as a GitHub repo secret.")
    return url


def xbl_headers(api_key: str) -> dict:
    return {
        "X-Authorization": api_key,
        "Accept-Language": "en-US",
        "Accept": "application/json",
    }


def matches_game(title_name: str) -> bool:
    return GAME_FILTER in title_name.lower()


# ── API helpers ────────────────────────────────────────────────────────────────

async def resolve_xuid(
    session: aiohttp.ClientSession, api_key: str, gamertag: str
) -> str | None:
    url = f"https://xbl.io/api/v2/friends/search?gt={aiohttp.helpers.quote(gamertag)}"
    try:
        async with session.get(url, headers=xbl_headers(api_key)) as resp:
            if resp.status != 200:
                print(f"[WARN] XUID lookup for '{gamertag}' returned HTTP {resp.status}")
                return None
            data = await resp.json()
            profile_users = data.get("content", {}).get("profileUsers", [])
            if not profile_users:
                people = data.get("people", [])
                if people:
                    return people[0].get("xuid")
                print(f"[WARN] No XUID found for '{gamertag}'")
                return None
            return profile_users[0].get("id")
    except aiohttp.ClientError as exc:
        print(f"[ERROR] Network error resolving XUID for '{gamertag}': {exc}")
        return None


async def get_xuid(
    session: aiohttp.ClientSession, api_key: str, gamertag: str, state: dict
) -> str | None:
    """Return a cached XUID if we have one, otherwise resolve and cache it.

    Gamertag->XUID never changes, so caching means after the first run we make
    zero resolve calls — keeping us comfortably inside the free tier."""
    cached = state["xuids"].get(gamertag)
    if cached:
        return cached
    xuid = await resolve_xuid(session, api_key, gamertag)
    if xuid:
        state["xuids"][gamertag] = xuid
        print(f"[INFO] Cached '{gamertag}' -> xuid={xuid}")
    return xuid


async def fetch_clips(
    session: aiohttp.ClientSession, api_key: str, xuid: str
) -> list[dict]:
    url = f"https://xbl.io/api/v2/dvr/gameclips/{xuid}"
    try:
        async with session.get(url, headers=xbl_headers(api_key)) as resp:
            if resp.status != 200:
                print(f"[WARN] Clips endpoint ({xuid}) returned HTTP {resp.status}")
                return []
            data = await resp.json()
            return data.get("content", {}).get("gameClips", [])
    except aiohttp.ClientError as exc:
        print(f"[ERROR] Network error fetching clips for xuid={xuid}: {exc}")
        return []


async def fetch_screenshots(
    session: aiohttp.ClientSession, api_key: str, xuid: str
) -> list[dict]:
    url = f"https://xbl.io/api/v2/dvr/screenshots/{xuid}"
    try:
        async with session.get(url, headers=xbl_headers(api_key)) as resp:
            if resp.status != 200:
                print(f"[WARN] Screenshots endpoint ({xuid}) returned HTTP {resp.status}")
                return []
            data = await resp.json()
            return data.get("content", {}).get("screenshots", [])
    except aiohttp.ClientError as exc:
        print(f"[ERROR] Network error fetching screenshots for xuid={xuid}: {exc}")
        return []


# ── Discord payload builders ───────────────────────────────────────────────────

def build_clip_payload(clip: dict, gamertag: str) -> tuple[str, dict]:
    clip_id       = clip.get("gameClipId", "unknown")
    game_name     = clip.get("titleName", "Unknown Game")
    date_recorded = clip.get("dateRecorded", "")
    duration      = clip.get("durationInSeconds", 0)
    views         = clip.get("views", 0)
    likes         = clip.get("likeCount", 0)

    thumbnails = clip.get("thumbnails", [])
    thumb_url  = thumbnails[0].get("uri", "") if thumbnails else ""

    mp4_url = ""
    for u in clip.get("gameClipUris", []):
        if u.get("fileSize", 0) > 0:
            mp4_url = u.get("uri", "")
            break

    content = (
        f"🎮 **{gamertag}** just posted a new clip!\n{mp4_url}"
        if mp4_url
        else f"🎮 **{gamertag}** just posted a new clip!"
    )

    fields = [
        {"name": "Gamertag", "value": gamertag, "inline": True},
        {"name": "Game",     "value": game_name, "inline": True},
        {"name": "Duration", "value": f"{duration}s", "inline": True},
        {"name": "Views / Likes", "value": f"{views} 👀  •  {likes} ❤️", "inline": True},
        {"name": "Clip ID",  "value": f"`{clip_id}`", "inline": False},
    ]
    if date_recorded:
        fields.append({"name": "Recorded", "value": date_recorded[:10], "inline": True})

    embed: dict = {
        "title": f"🎬  {game_name} — Game Clip",
        "color": COLOR_GREEN,
        "fields": fields,
        "footer": {"text": "Xbox DVR  •  College Football 27 Tracker"},
    }
    if date_recorded:
        embed["timestamp"] = date_recorded
    if thumb_url:
        embed["thumbnail"] = {"url": thumb_url}

    return clip_id, {"content": content, "embeds": [embed], "username": "Xbox DVR Bot"}


def build_screenshot_payload(shot: dict, gamertag: str) -> tuple[str, dict]:
    shot_id    = shot.get("screenshotId", "unknown")
    game_name  = shot.get("titleName", "Unknown Game")
    date_taken = shot.get("dateTaken", "")
    views      = shot.get("views", 0)
    width      = shot.get("resolutionWidth", 0)
    height     = shot.get("resolutionHeight", 0)

    thumbnails = shot.get("thumbnails", [])
    thumb_url  = thumbnails[0].get("uri", "") if thumbnails else ""

    uris     = shot.get("screenshotUris", [])
    full_url = uris[0].get("uri", "") if uris else ""

    fields = [
        {"name": "Gamertag",   "value": gamertag, "inline": True},
        {"name": "Game",       "value": game_name, "inline": True},
        {"name": "Resolution", "value": f"{width}×{height}", "inline": True},
        {"name": "Views",      "value": str(views), "inline": True},
        {"name": "Screenshot ID", "value": f"`{shot_id}`", "inline": False},
    ]
    if date_taken:
        fields.append({"name": "Taken", "value": date_taken[:10], "inline": True})

    embed: dict = {
        "title": f"📷  {game_name} — Screenshot",
        "color": COLOR_BLUE,
        "fields": fields,
        "footer": {"text": "Xbox DVR  •  College Football 27 Tracker"},
    }
    if date_taken:
        embed["timestamp"] = date_taken
    image_url = full_url or thumb_url
    if image_url:
        embed["image"] = {"url": image_url}

    return shot_id, {
        "content": f"📸 **{gamertag}** just posted a new screenshot!",
        "embeds": [embed],
        "username": "Xbox DVR Bot",
    }


# ── Discord posting ────────────────────────────────────────────────────────────

async def post_to_discord(
    session: aiohttp.ClientSession, webhook_url: str, payload: dict, label: str
) -> bool:
    try:
        async with session.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status in (200, 204):
                print(f"[OK]   Posted → {label}")
                return True
            body = await resp.text()
            print(f"[WARN] Discord {resp.status} for {label}: {body[:200]}")
            return False
    except aiohttp.ClientError as exc:
        print(f"[ERROR] Discord post failed for {label}: {exc}")
        return False


# ── Per-gamertag scan ──────────────────────────────────────────────────────────

async def scan_gamertag(
    session: aiohttp.ClientSession,
    api_key: str,
    webhook_url: str,
    gamertag: str,
    state: dict,
) -> set:
    """Scan one gamertag and return a set of newly posted IDs."""
    new_ids: set[str] = set()

    xuid = await get_xuid(session, api_key, gamertag, state)
    if not xuid:
        return new_ids

    clips, screenshots = await asyncio.gather(
        fetch_clips(session, api_key, xuid),
        fetch_screenshots(session, api_key, xuid),
    )

    cf_clips = [c for c in clips if matches_game(c.get("titleName", ""))]
    cf_shots = [s for s in screenshots if matches_game(s.get("titleName", ""))]

    print(
        f"[INFO] {gamertag}: {len(clips)} clips ({len(cf_clips)} CF27), "
        f"{len(screenshots)} screenshots ({len(cf_shots)} CF27)"
    )

    for clip in cf_clips:
        clip_id, payload = build_clip_payload(clip, gamertag)
        if clip_id in state["processed"] or clip_id in new_ids:
            continue
        if await post_to_discord(session, webhook_url, payload, f"{gamertag}/clip:{clip_id[:8]}"):
            new_ids.add(clip_id)
        await asyncio.sleep(1)

    for shot in cf_shots:
        shot_id, payload = build_screenshot_payload(shot, gamertag)
        if shot_id in state["processed"] or shot_id in new_ids:
            continue
        if await post_to_discord(session, webhook_url, payload, f"{gamertag}/shot:{shot_id[:8]}"):
            new_ids.add(shot_id)
        await asyncio.sleep(1)

    return new_ids


# ── Single run ─────────────────────────────────────────────────────────────────

async def run() -> None:
    api_key = get_api_key()
    webhook_url = get_webhook_url()
    state = load_state()

    print("[START] College Football 27 Xbox clip watch")
    print(f"[START] Tracking {len(GAMERTAGS)} gamertags: {', '.join(GAMERTAGS)}")

    all_new: set[str] = set()
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        for gamertag in GAMERTAGS:
            try:
                new_ids = await scan_gamertag(
                    session, api_key, webhook_url, gamertag, state
                )
                all_new |= new_ids
            except Exception as exc:
                print(f"[ERROR] Unhandled error scanning '{gamertag}': {exc}")
            await asyncio.sleep(1)  # polite gap between accounts

    state["processed"] |= all_new
    save_state(state)

    if all_new:
        print(f"[DONE] Posted and saved {len(all_new)} new item(s).")
    else:
        print("[DONE] No new College Football 27 media this run.")


if __name__ == "__main__":
    asyncio.run(run())
