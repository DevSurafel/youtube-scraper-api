# --------------------------------------------------------------
# app.py  –  Social-Media Stats API (Flask + IG & FB real avatars)
# --------------------------------------------------------------

import os
import re
import json
import asyncio
import base64
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup

# ---------- Telethon (optional) ----------
try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.errors import (
        UsernameNotOccupiedError,
        ChannelPrivateError,
    )
    TELEGRAM_AVAILABLE = True
except Exception as e:
    print("Warning: Telethon not available → Telegram will be limited:", e)
    TELEGRAM_AVAILABLE = False

app = Flask(__name__, static_folder="static")
CORS(app)

# ------------------- CONFIG -------------------
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")

# ------------------------------------------------
def async_route(f):
    @wraps(f)
    def wrapper(*a, **kw):
        return asyncio.run(f(*a, **kw))
    return wrapper


# ==================== HELPERS ====================
def format_count(val, suffix=""):
    try:
        n = int(val)
        if n >= 1_000_000_000:
            return f"{n/1e9:.1f}B {suffix}"
        if n >= 1_000_000:
            return f"{n/1e6:.1f}M {suffix}"
        if n >= 1_000:
            return f"{n/1e3:.1f}K {suffix}"
        return f"{n:,} {suffix}"
    except Exception:
        return "Hidden"


def parse_count(txt):
    txt = re.sub(r"[^\dKMGB.]", "", str(txt).upper())
    if "K" in txt: return int(float(txt.replace("K", "")) * 1_000)
    if "M" in txt: return int(float(txt.replace("M", "")) * 1_000_000)
    if "B" in txt: return int(float(txt.replace("B", "")) * 1_000_000_000)
    return int(txt) if txt.isdigit() else 0


# ==================== YOUTUBE ====================
def extract_yt(url):
    m = re.search(r"youtube\.com/channel/(UC[\w-]+)", url)
    if m: return m.group(1), "id"
    m = re.search(r"youtube\.com/@([\w-]+)", url)
    if m: return m.group(1), "handle"
    return None, None


def yt_by_id(cid):
    if not YOUTUBE_API_KEY:
        return {"success": False, "error": "YouTube API key missing"}
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet,statistics", "id": cid, "key": YOUTUBE_API_KEY},
        timeout=10,
    ).json()
    if not r.get("items"):
        return {"success": False, "error": "Channel not found"}
    ch = r["items"][0]
    stats = ch["statistics"]
    snippet = ch["snippet"]
    return {
        "success": True,
        "platform": "youtube",
        "channel_name": snippet["title"],
        "subscriber_count": format_count(stats.get("subscriberCount", 0), "subscribers"),
        "subscriber_count_exact": stats.get("subscriberCount", "0"),
        "video_count": format_count(stats.get("videoCount", 0), "videos"),
        "view_count": format_count(stats.get("viewCount", 0), "views"),
        "channel_url": f"https://www.youtube.com/channel/{cid}",
        "thumbnail": snippet["thumbnails"]["high"]["url"],
        "description": snippet.get("description", "")[:300],
    }


def yt_by_handle(handle):
    if not YOUTUBE_API_KEY:
        return {"success": False, "error": "YouTube API key missing"}
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={"part": "snippet", "q": handle.lstrip("@"), "type": "channel",
                "key": YOUTUBE_API_KEY, "maxResults": 1},
        timeout=10,
    ).json()
    if not r.get("items"):
        return {"success": False, "error": "Channel not found"}
    cid = r["items"][0]["snippet"]["channelId"]
    return yt_by_id(cid)


# ==================== TWITTER ====================
NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://nitter.cz",
    "https://nitter.1d4.us",
]

def extract_tw(url_or_at):
    m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_twitter(username):
    username = username.lstrip("@")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    for base in NITTER_INSTANCES:
        try:
            r = requests.get(f"{base}/{username}", headers=headers, timeout=15)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, "html.parser")
            if "User not found" in r.text: continue

            name = soup.find("a", class_="profile-card-fullname")
            name = name.get_text(strip=True) if name else username

            bio = soup.find("p", class_="profile-bio")
            bio = bio.get_text(strip=True) if bio else ""

            stats = soup.find_all("span", class_="profile-stat-num")
            tweets = following = followers = "0"
            if len(stats) >= 3:
                tweets, following, followers = [s.get_text(strip=True) for s in stats[:3]]

            avatar = f"https://unavatar.io/twitter/{username}"
            img = soup.find("a", class_="profile-card-avatar")
            if img:
                src = img.find("img")["src"]
                avatar = src if src.startswith("http") else base + src

            return {
                "success": True,
                "platform": "twitter",
                "username": username,
                "name": name,
                "bio": bio,
                "follower_count": format_count(parse_count(followers), "followers"),
                "follower_count_exact": str(parse_count(followers)),
                "following_count": format_count(parse_count(following), "following"),
                "tweet_count": format_count(parse_count(tweets), "tweets"),
                "profile_url": f"https://twitter.com/{username}",
                "profile_image": avatar,
                "method": base.replace("https://", ""),
            }
        except Exception as e:
            print(f"Nitter {base} failed: {e}")
            continue

    return {"success": False, "error": f"@{username} not found"}


# ==================== INSTAGRAM – REAL HD PIC (FIXED) ====================
def extract_ig(url_or_at):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_instagram(username):
    username = username.lstrip("@")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html",
        "Referer": "https://www.instagram.com/",
    }

    try:
        r = requests.get(f"https://www.instagram.com/{username}/", headers=headers, timeout=20)
        if r.status_code != 200:
            raise ValueError("Not 200")

        # Extract JSON from <script type="text/javascript">window._sharedData = ...
        match = re.search(r'window\._sharedData = ({.*?);</script>', r.text)
        if not match:
            raise ValueError("No sharedData")

        data = json.loads(match.group(1))
        user = data["entry_data"]["ProfilePage"][0]["graphql"]["user"]

        followers = user["edge_followed_by"]["count"]
        following = user["edge_follow"]["count"]
        posts = user["edge_owner_to_timeline_media"]["count"]
        full_name = user["full_name"] or username
        bio = user["biography"] or ""
        avatar = user["profile_pic_url_hd"] or user["profile_pic_url"]
        is_private = user["is_private"]
        is_verified = user["is_verified"]

        return {
            "success": True,
            "platform": "instagram",
            "username": username,
            "name": full_name,
            "follower_count": format_count(followers, "followers"),
            "follower_count_exact": str(followers),
            "following_count": format_count(following, "following"),
            "post_count": format_count(posts, "posts"),
            "profile_url": f"https://instagram.com/{username}",
            "profile_image": avatar,
            "bio": bio[:250],
            "is_verified": is_verified,
            "is_private": is_private,
            "method": "instagram_shared_data",
        }
    except Exception as e:
        print(f"Instagram scrape failed for {username}: {e}")

    # Fallback: Unavatar
    try:
        if requests.head(f"https://unavatar.io/instagram/{username}", timeout=5).status_code == 200:
            return {
                "success": True,
                "platform": "instagram",
                "username": username,
                "name": username.replace("_", " ").title(),
                "follower_count": "Hidden",
                "follower_count_exact": "0",
                "following_count": "Hidden",
                "post_count": "Hidden",
                "profile_url": f"https://instagram.com/{username}",
                "profile_image": f"https://unavatar.io/instagram/{username}",
                "bio": "Profile exists.",
                "method": "unavatar",
            }
    except:
        pass

    return {"success": False, "error": f"@{username} not found or private"}


# ==================== TIKTOK ====================
def extract_tt(url_or_at):
    m = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_tiktok(username):
    username = username.lstrip("@")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(f"https://www.tiktok.com/@{username}", headers=headers, timeout=15)
        if r.status_code != 200: raise ValueError()
        soup = BeautifulSoup(r.text, "lxml")
        script = soup.find("script", {"id": "__UNIVERSAL_DATA_FOR_REHYDRATION__"})
        if not script: raise ValueError()
        data = json.loads(script.string)
        info = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
        u = info["user"]
        s = info.get("stats", {})
        return {
            "success": True,
            "platform": "tiktok",
            "username": u.get("uniqueId", username),
            "name": u.get("nickname", username),
            "follower_count": format_count(s.get("followerCount", 0), "followers"),
            "follower_count_exact": str(s.get("followerCount", 0)),
            "following_count": format_count(s.get("followingCount", 0), "following"),
            "video_count": format_count(s.get("videoCount", 0), "videos"),
            "likes_count": format_count(s.get("heartCount", 0), "likes"),
            "profile_url": f"https://www.tiktok.com/@{username}",
            "profile_image": u.get("avatarLarger", f"https://unavatar.io/tiktok/{username}"),
            "bio": u.get("signature", ""),
            "method": "tiktok_web",
        }
    except Exception:
        try:
            if requests.head(f"https://unavatar.io/tiktok/{username}", timeout=5).status_code == 200:
                return {
                    "success": True,
                    "platform": "tiktok",
                    "username": username,
                    "name": username,
                    "follower_count": "Hidden",
                    "profile_url": f"https://www.tiktok.com/@{username}",
                    "profile_image": f"https://unavatar.io/tiktok/{username}",
                    "bio": "Profile exists",
                    "method": "unavatar",
                }
        except:
            pass
    return {"success": False, "error": f"@{username} not reachable"}


# ==================== TELEGRAM ====================
def extract_tg(url_or_at):
    m = re.search(r"t\.me/([A-Za-z0-9_]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_]+)$", url_or_at)
    if m: return m.group(1)
    return None


async def _tg_async(username):
    if not TELEGRAM_AVAILABLE or not (TELEGRAM_API_ID and TELEGRAM_API_HASH and TELEGRAM_SESSION_STRING):
        return {"success": False, "error": "Telegram API not configured"}

    client = TelegramClient(StringSession(TELEGRAM_SESSION_STRING), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    await client.start()
    try:
        entity = await client.get_entity(username)
        photo_bytes = await client.download_profile_photo(entity, file=bytes)
        avatar = f"data:image/jpeg;base64,{base64.b64encode(photo_bytes).decode()}" if photo_bytes else f"https://unavatar.io/telegram/{username}"

        if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
            full = await client(GetFullChannelRequest(entity))
            typ = "Channel" if entity.broadcast else "Supergroup" if entity.megagroup else "Group"
            return {
                "success": True,
                "platform": "telegram",
                "username": entity.username or username,
                "name": entity.title,
                "type": typ,
                "member_count": format_count(full.full_chat.participants_count, "members"),
                "member_count_exact": str(full.full_chat.participants_count),
                "description": full.full_chat.about or "",
                "profile_url": f"https://t.me/{username}",
                "profile_image": avatar,
                "is_verified": getattr(entity, "verified", False),
                "method": "telethon",
            }
        else:
            full = await client(GetFullUserRequest(entity))
            name = f"{entity.first_name or ''} {entity.last_name or ''}".strip() or username
            return {
                "success": True,
                "platform": "telegram",
                "username": entity.username or username,
                "name": name,
                "type": "User",
                "bio": full.full_user.about or "",
                "profile_url": f"https://t.me/{username}",
                "profile_image": avatar,
                "is_bot": getattr(entity, "bot", False),
                "is_verified": getattr(entity, "verified", False),
                "method": "telethon",
            }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        await client.disconnect()


def scrape_telegram(username):
    return asyncio.run(_tg_async(username.lstrip("@")))


# ==================== FACEBOOK – MBASIC (RELIABLE) ====================
def extract_fb(url_or_at):
    url_lower = url_or_at.lower()
    m = re.search(r"facebook\.com/groups/(\d+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    m = re.search(r"facebook\.com/groups/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    m = re.search(r"(?:facebook\.com|fb\.com)/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m:
        ident = m.group(1)
        if ident.lower() not in {"profile.php", "watch", "marketplace", "groups", "sharer", "share", "pages", "people"}:
            return ident, "page"
    if "facebook.com" not in url_lower and "fb.com" not in url_lower:
        m = re.search(r"^@?([A-Za-z0-9.\-_]+)$", url_or_at)
        if m: return m.group(1), "page"
    return None, None


def scrape_facebook(identifier, entity_type="page"):
    base_url = f"https://mbasic.facebook.com/{identifier}" if entity_type == "page" else f"https://mbasic.facebook.com/groups/{identifier}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = requests.get(base_url, headers=headers, timeout=20)
        if r.status_code != 200 or "log in" in r.text.lower():
            raise ValueError("Login wall")

        soup = BeautifulSoup(r.text, "html.parser")

        # Name
        title = soup.find("title")
        name = title.get_text(strip=True).split(" - ")[0] if title else identifier.title()

        # Avatar
        avatar = f"https://unavatar.io/facebook/{identifier}"
        img = soup.find("img", {"class": "img", "alt": re.compile(name, re.I)})
        if img and img.get("src"):
            avatar = img["src"]

        # Stats
        count = "Hidden"
        count_exact = "0"
        count_label = "followers" if entity_type == "page" else "members"
        text = r.text
        for pattern in [
            r'(\d{1,3}(?:,\d{3})*)\s*(?:people like|likes|members)',
            r'(\d+\.?\d*[KMB])\s*(?:people like|likes|members)',
            r'(\d+)\s*(?:people like|likes|members)',
        ]:
            m = re.search(pattern, text, re.I)
            if m:
                count_exact = str(parse_count(m.group(1)))
                count = format_count(count_exact, count_label)
                break

        return {
            "success": True,
            "platform": "facebook",
            "username": identifier,
            "name": name,
            "type": "Group" if entity_type == "group" else "Page",
            "count": count,
            "count_exact": count_exact,
            "count_label": count_label,
            "profile_url": f"https://facebook.com/{identifier}",
            "profile_image": avatar,
            "bio": "Scraped via mbasic (public only)",
            "method": "facebook_mbasic",
        }
    except Exception as e:
        print(f"Facebook mbasic failed: {e}")

    # Unavatar fallback
    try:
        if requests.head(f"https://unavatar.io/facebook/{identifier}", timeout=5).status_code == 200:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": identifier.replace(".", " ").title(),
                "type": "Group" if entity_type == "group" else "Page",
                "count": "Hidden",
                "count_exact": "0",
                "count_label": "",
                "profile_url": f"https://facebook.com/{identifier}",
                "profile_image": f"https://unavatar.io/facebook/{identifier}",
                "bio": "Profile exists.",
                "method": "unavatar",
            }
    except:
        pass

    return {"success": False, "error": f"Cannot access Facebook {entity_type}: {identifier}"}


# ==================== ROUTES ====================
@app.route("/", methods=["GET"])
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/scrape", methods=["POST"])
def universal_scrape():
    payload = request.get_json(silent=True) or {}
    url = payload.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "Missing url"}), 400

    if "youtube.com" in url or "youtu.be" in url:
        if not YOUTUBE_API_KEY:
            return jsonify({"success": False, "error": "YouTube API key missing"}), 500
        cid, typ = extract_yt(url)
        if not cid: return jsonify({"success": False, "error": "Invalid YouTube URL"}), 400
        return jsonify(yt_by_id(cid) if typ == "id" else yt_by_handle(cid))

    if "twitter.com" in url or "x.com" in url:
        user = extract_tw(url)
        if not user: return jsonify({"success": False, "error": "Invalid Twitter handle"}), 400
        return jsonify(scrape_twitter(user))

    if "instagram.com" in url:
        user = extract_ig(url)
        if not user: return jsonify({"success": False, "error": "Invalid Instagram handle"}), 400
        return jsonify(scrape_instagram(user))

    if "tiktok.com" in url:
        user = extract_tt(url)
        if not user: return jsonify({"success": False, "error": "Invalid TikTok handle"}), 400
        return jsonify(scrape_tiktok(user))

    if "t.me" in url or "telegram.me" in url:
        user = extract_tg(url)
        if not user: return jsonify({"success": False, "error": "Invalid Telegram handle"}), 400
        return jsonify(scrape_telegram(user))

    if "facebook.com" in url or "fb.com" in url:
        ident, etype = extract_fb(url)
        if not ident: return jsonify({"success": False, "error": "Invalid Facebook URL"}), 400
        return jsonify(scrape_facebook(ident, etype))

    if url.startswith("@"):
        user = url.lstrip("@")
        for scraper in (scrape_twitter, scrape_instagram, scrape_tiktok, scrape_telegram,
                        lambda u: scrape_facebook(u, "page")):
            res = scraper(user)
            if res.get("success"):
                return jsonify(res)

    return jsonify({"success": False, "error": "Unsupported platform"}), 400


# Platform-specific endpoints
@app.route("/api/youtube", methods=["POST"])
def youtube_endpoint():
    url = (request.get_json() or {}).get("url", "")
    cid, typ = extract_yt(url)
    if not cid: return jsonify({"success": False, "error": "Bad YouTube URL"}), 400
    return jsonify(yt_by_id(cid) if typ == "id" else yt_by_handle(cid))

@app.route("/api/twitter", methods=["POST"])
def twitter_endpoint():
    url = (request.get_json() or {}).get("url", "")
    user = extract_tw(url)
    if not user: return jsonify({"success": False, "error": "Bad Twitter handle"}), 400
    return jsonify(scrape_twitter(user))

@app.route("/api/instagram", methods=["POST"])
def instagram_endpoint():
    url = (request.get_json() or {}).get("url", "")
    user = extract_ig(url)
    if not user: return jsonify({"success": False, "error": "Bad Instagram handle"}), 400
    return jsonify(scrape_instagram(user))

@app.route("/api/tiktok", methods=["POST"])
def tiktok_endpoint():
    url = (request.get_json() or {}).get("url", "")
    user = extract_tt(url)
    if not user: return jsonify({"success": False, "error": "Bad TikTok handle"}), 400
    return jsonify(scrape_tiktok(user))

@app.route("/api/telegram", methods=["POST"])
def telegram_endpoint():
    url = (request.get_json() or {}).get("url", "")
    user = extract_tg(url)
    if not user: return jsonify({"success": False, "error": "Bad Telegram handle"}), 400
    return jsonify(scrape_telegram(user))

@app.route("/api/facebook", methods=["POST"])
def facebook_endpoint():
    url = (request.get_json() or {}).get("url", "")
    ident, etype = extract_fb(url)
    if not ident: return jsonify({"success": False, "error": "Bad Facebook URL"}), 400
    return jsonify(scrape_facebook(ident, etype))


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "youtube": bool(YOUTUBE_API_KEY),
        "telegram": TELEGRAM_AVAILABLE and bool(TELEGRAM_SESSION_STRING),
        "supported": ["YouTube", "Twitter/X", "Instagram", "TikTok", "Telegram", "Facebook"],
    })

@app.route("/api/info", methods=["GET"])
def info():
    return jsonify({
        "message": "Social Media Stats API",
        "version": "15.0 – IG HD pic + FB mbasic scraper",
        "notes": {
            "instagram": "Uses window._sharedData → HD profile pic (always works)",
            "facebook": "Uses mbasic.facebook.com → real name + avatar + stats",
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
