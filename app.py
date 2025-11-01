# --------------------------------------------------------------
# app.py  –  Social-Media Stats API (Flask + Telethon + FB Groups/Pages)
# --------------------------------------------------------------

import os
import re
import json
import asyncio
import time
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
        UsernameInvalidError,
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
    return {
        "success": True,
        "platform": "youtube",
        "channel_name": ch["snippet"]["title"],
        "subscriber_count": format_count(stats.get("subscriberCount", 0), "subscribers"),
        "subscriber_count_exact": stats.get("subscriberCount", "0"),
        "channel_url": f"https://www.youtube.com/channel/{cid}",
        "thumbnail": ch["snippet"]["thumbnails"]["high"]["url"],
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


# ==================== TWITTER - IMPROVED ====================
NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://nitter.cz",
    "https://nitter.1d4.us",
    "https://bird.makeup",
    "https://nitter.it",
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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    # Try Nitter instances
    for base in NITTER_INSTANCES:
        try:
            r = requests.get(f"{base}/{username}", headers=headers, timeout=15)
            if r.status_code != 200: 
                continue
            
            soup = BeautifulSoup(r.text, "lxml")

            # Extract name
            name = username
            name_elem = (soup.find("a", class_="profile-card-fullname") or
                        soup.find("div", class_="profile-card-fullname"))
            if name_elem:
                name = name_elem.get_text(strip=True)
            elif soup.find("h2"):
                name = soup.find("h2").get_text(strip=True)

            # Extract bio
            bio = ""
            bio_elem = soup.find("p", class_="profile-bio")
            if bio_elem:
                bio = bio_elem.get_text(strip=True)

            # Extract stats
            tweets = following = followers = "0"
            
            # Try method 1: profile-stat-num spans
            stats = soup.find_all("span", class_="profile-stat-num")
            if len(stats) >= 3:
                tweets, following, followers = [s.get_text(strip=True) for s in stats[:3]]
            else:
                # Try method 2: profile-statlist
                for li in soup.select("ul.profile-statlist li"):
                    lbl = li.find("span", class_="profile-stat-header")
                    val = li.find("span", class_="profile-stat-num")
                    if not (lbl and val): continue
                    txt = lbl.get_text().lower()
                    if "tweet" in txt or "post" in txt: 
                        tweets = val.get_text(strip=True)
                    elif "following" in txt: 
                        following = val.get_text(strip=True)
                    elif "follower" in txt: 
                        followers = val.get_text(strip=True)

            follower_cnt = parse_count(followers)

            # Extract avatar
            avatar = f"https://unavatar.io/twitter/{username}"
            img_elem = soup.find("a", class_="profile-card-avatar")
            if img_elem and img_elem.find("img"):
                src = img_elem.find("img").get("src", "")
                if src:
                    avatar = src if src.startswith("http") else base + src

            # Check if we got valid data
            if follower_cnt > 0 or name != username or bio:
                return {
                    "success": True,
                    "platform": "twitter",
                    "username": username,
                    "name": name,
                    "bio": bio,
                    "follower_count": format_count(follower_cnt, "followers"),
                    "follower_count_exact": str(follower_cnt),
                    "following_count": format_count(parse_count(following), "following"),
                    "tweet_count": format_count(parse_count(tweets), "tweets"),
                    "profile_url": f"https://twitter.com/{username}",
                    "profile_image": avatar,
                    "method": base.replace("https://", ""),
                }
        except Exception as e:
            print(f"Nitter {base} failed: {e}")
            continue

    # Try Twitter Syndication API
    try:
        r = requests.get(
            f"https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={username}",
            headers=headers, 
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data and len(data) > 0:
                user_data = data[0]
                return {
                    "success": True,
                    "platform": "twitter",
                    "username": username,
                    "name": user_data.get("name", username),
                    "follower_count": format_count(user_data.get("followers_count", 0), "followers"),
                    "follower_count_exact": str(user_data.get("followers_count", 0)),
                    "following_count": "Hidden",
                    "tweet_count": "Hidden",
                    "profile_url": f"https://twitter.com/{username}",
                    "profile_image": f"https://unavatar.io/twitter/{username}",
                    "bio": "",
                    "method": "syndication",
                }
    except Exception as e:
        print(f"Syndication API failed: {e}")

    # Try ScraperAPI/ProxyCurl alternative
    try:
        # Using a public Twitter profile viewer
        r = requests.get(
            f"https://mobile.twitter.com/{username}",
            headers={
                **headers,
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"
            },
            timeout=15,
            allow_redirects=True
        )
        
        if r.status_code == 200:
            # Try to extract basic info from mobile view
            soup = BeautifulSoup(r.text, "lxml")
            
            # Check if profile exists (not suspended/doesn't exist)
            if "This account doesn't exist" not in r.text and "Account suspended" not in r.text:
                return {
                    "success": True,
                    "platform": "twitter",
                    "username": username,
                    "name": username,
                    "follower_count": "Hidden",
                    "following_count": "Hidden",
                    "tweet_count": "Hidden",
                    "profile_url": f"https://twitter.com/{username}",
                    "profile_image": f"https://unavatar.io/twitter/{username}",
                    "bio": "Profile exists but stats unavailable (try again later)",
                    "method": "mobile_fallback",
                }
    except Exception as e:
        print(f"Mobile fallback failed: {e}")

    # Final fallback: Check if avatar exists
    try:
        avatar_check = requests.head(f"https://unavatar.io/twitter/{username}", timeout=5)
        if avatar_check.status_code == 200:
            return {
                "success": True,
                "platform": "twitter",
                "username": username,
                "name": username,
                "follower_count": "Hidden",
                "following_count": "Hidden",
                "tweet_count": "Hidden",
                "profile_url": f"https://twitter.com/{username}",
                "profile_image": f"https://unavatar.io/twitter/{username}",
                "bio": "Profile exists but stats unavailable",
                "method": "unavatar",
            }
    except Exception:
        pass

    return {"success": False, "error": f"@{username} not reachable or doesn't exist"}


# ==================== TIKTOK ====================
def extract_tt(url_or_at):
    m = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_tiktok(username):
    username = username.lstrip("@")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

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
                    "bio": "",
                    "method": "unavatar",
                }
        except Exception:
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

    client = TelegramClient(
        StringSession(TELEGRAM_SESSION_STRING),
        int(TELEGRAM_API_ID),
        TELEGRAM_API_HASH,
    )
    await client.start()

    try:
        entity = await client.get_entity(username)
        avatar = f"https://unavatar.io/telegram/{username}"

        if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
            full = await client(GetFullChannelRequest(entity))
            typ = ("Channel" if entity.broadcast else
                   "Supergroup" if entity.megagroup else "Group")
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

        full = await client(GetFullUserRequest(entity))
        name = f"{entity.first_name or ''} {entity.last_name or ''}".strip() or username
        return {
            "success": True,
            "platform": "telegram",
            "username": entity.username or username,
            "name": name,
            "type": "User",
            "user_id": entity.id,
            "bio": full.full_user.about or "",
            "profile_url": f"https://t.me/{username}",
            "profile_image": avatar,
            "is_bot": getattr(entity, "bot", False),
            "is_verified": getattr(entity, "verified", False),
            "method": "telethon",
            "note": "Users have no public follower count",
        }

    except UsernameNotOccupiedError:
        return {"success": False, "error": f"@{username} not found"}
    except ChannelPrivateError:
        return {"success": False, "error": f"@{username} is private"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        await client.disconnect()


def scrape_telegram(username):
    return asyncio.run(_tg_async(username.lstrip("@")))


# ==================== FACEBOOK - IMPROVED ====================
def extract_fb(url_or_at):
    """Extract identifier from Facebook URL with improved group detection"""
    # Check for group URL first
    m = re.search(r"facebook\.com/groups/([A-Za-z0-9.\-_]+)", url_or_at)
    if m:
        return m.group(1), "group"
    
    # Check for numeric group ID
    m = re.search(r"facebook\.com/groups/(\d+)", url_or_at)
    if m:
        return m.group(1), "group"
    
    # Check for page/profile
    m = re.search(r"(?:facebook\.com|fb\.com)/([A-Za-z0-9.\-_]+)", url_or_at)
    if m:
        ident = m.group(1)
        if ident.lower() not in {"profile.php", "watch", "marketplace", "groups"}:
            return ident, "page"
    
    # Handle plain username
    m = re.search(r"^@?([A-Za-z0-9.\-_]+)$", url_or_at)
    if m:
        return m.group(1), "page"
    
    return None, None


def scrape_facebook(identifier, entity_type="page"):
    """Improved Facebook scraper with better group/page detection"""
    
    # Construct proper URL
    if entity_type == "group":
        url = f"https://www.facebook.com/groups/{identifier}"
    else:
        url = f"https://www.facebook.com/{identifier}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")

        soup = BeautifulSoup(r.text, "lxml")
        text = r.text

        # Extract title
        title_tag = soup.find("title")
        raw_title = title_tag.get_text(strip=True) if title_tag else ""
        name = re.sub(r"\s*[-|·]\s*Facebook$", "", raw_title).strip()
        
        # Remove "Log into Facebook" prefix if present
        name = re.sub(r"^Log into Facebook\s*[-|·]?\s*", "", name).strip()

        # Determine if it's a group or page
        is_group = entity_type == "group" or "/groups/" in url or "/groups/" in r.url

        count = "Hidden"
        count_exact = "0"
        count_label = ""

        if is_group:
            # Group: Look for members
            patterns = [
                r"([\d,\.]+[KMB]?)\s+members?",
                r"members?\s*[:\-]?\s*([\d,\.]+[KMB]?)",
                r'"member_count":\s*(\d+)',
                r'"group_member_count":\s*(\d+)',
            ]
            
            for pattern in patterns:
                m = re.search(pattern, text, re.I)
                if m:
                    count_str = m.group(1).replace(",", "").replace(".", "")
                    count_exact = str(parse_count(count_str))
                    if int(count_exact) > 0:
                        count = format_count(count_exact, "members")
                        count_label = "members"
                        break
            
            entity_type_label = "Group"
        else:
            # Page: Look for followers or likes
            patterns = [
                (r"([\d,\.]+[KMB]?)\s+followers?", "followers"),
                (r"followers?\s*[:\-]?\s*([\d,\.]+[KMB]?)", "followers"),
                (r"([\d,\.]+[KMB]?)\s+(?:people\s+)?likes?", "likes"),
                (r'"follower_count":\s*(\d+)', "followers"),
                (r'"page_likers":\s*(\d+)', "likes"),
            ]
            
            for pattern, label in patterns:
                m = re.search(pattern, text, re.I)
                if m:
                    count_str = m.group(1).replace(",", "").replace(".", "")
                    count_exact = str(parse_count(count_str))
                    if int(count_exact) > 0:
                        count = format_count(count_exact, label)
                        count_label = label
                        break
            
            entity_type_label = "Page"

        # Extract description
        bio = ""
        meta_desc = soup.find("meta", {"name": "description"})
        if meta_desc:
            bio = meta_desc.get("content", "")[:200]

        # Profile image
        img_url = f"https://unavatar.io/facebook/{identifier}"
        meta_img = soup.find("meta", property="og:image")
        if meta_img and meta_img.get("content"):
            img_url = meta_img["content"]

        # If we got meaningful data, return success
        if name and name != "Facebook" and name != identifier:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": name,
                "type": entity_type_label,
                "count": count,
                "count_exact": count_exact,
                "count_label": count_label,
                "profile_url": url,
                "profile_image": img_url,
                "bio": bio or "Facebook scraping is limited. Login may be required for full details.",
                "method": "basic_scrape",
                "note": "Public pages and groups only. Some data may be hidden." if count == "Hidden" else None
            }

    except Exception as e:
        print(f"FB scrape failed for {identifier}: {e}")

    # Fallback to unavatar
    try:
        avatar_url = f"https://unavatar.io/facebook/{identifier}"
        avatar_check = requests.head(avatar_url, timeout=5)
        if avatar_check.status_code == 200:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": identifier,
                "type": "Page/Group",
                "count": "Hidden",
                "count_exact": "0",
                "count_label": "",
                "profile_url": url,
                "profile_image": avatar_url,
                "bio": "Stats unavailable - login required",
                "method": "unavatar",
                "note": "Profile exists but detailed stats require login"
            }
    except Exception:
        pass

    return {"success": False, "error": f"Cannot access Facebook: {identifier}"}


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

    # YouTube
    if "youtube.com" in url or "youtu.be" in url:
        if not YOUTUBE_API_KEY:
            return jsonify({"success": False, "error": "YouTube API key missing"}), 500
        cid, typ = extract_yt(url)
        if not cid:
            return jsonify({"success": False, "error": "Invalid YouTube URL"}), 400
        return jsonify(yt_by_id(cid) if typ == "id" else yt_by_handle(cid))

    # Twitter / X
    if "twitter.com" in url or "x.com" in url or (url.startswith("@") and " " not in url):
        user = extract_tw(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid Twitter handle"}), 400
        return jsonify(scrape_twitter(user))

    # TikTok
    if "tiktok.com" in url:
        user = extract_tt(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid TikTok handle"}), 400
        return jsonify(scrape_tiktok(user))

    # Telegram
    if "t.me" in url or "telegram.me" in url:
        user = extract_tg(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid Telegram handle"}), 400
        return jsonify(scrape_telegram(user))

    # Facebook
    if "facebook.com" in url or "fb.com" in url:
        ident, entity_type = extract_fb(url)
        if not ident:
            return jsonify({"success": False, "error": "Invalid Facebook URL"}), 400
        return jsonify(scrape_facebook(ident, entity_type))

    # @handle auto-detect
    if url.startswith("@"):
        user = url.lstrip("@")
        
        # Try Twitter first
        res = scrape_twitter(user)
        if res.get("success"): 
            return jsonify(res)
        
        # Try TikTok
        res = scrape_tiktok(user)
        if res.get("success"): 
            return jsonify(res)
        
        # Try Telegram
        res = scrape_telegram(user)
        if res.get("success"): 
            return jsonify(res)
        
        # Try Facebook
        res = scrape_facebook(user, "page")
        if res.get("success"): 
            return jsonify(res)

    return jsonify({"success": False, "error": "Unsupported platform or invalid URL"}), 400


# Dedicated endpoints
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
    ident, entity_type = extract_fb(url)
    if not ident: return jsonify({"success": False, "error": "Bad Facebook URL"}), 400
    return jsonify(scrape_facebook(ident, entity_type))


# Health & Info
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "youtube": bool(YOUTUBE_API_KEY),
        "telegram": TELEGRAM_AVAILABLE and bool(TELEGRAM_SESSION_STRING),
        "supported": ["YouTube", "Twitter/X", "TikTok", "Telegram", "Facebook (Pages & Groups)"],
    })

@app.route("/api/info", methods=["GET"])
def info():
    return jsonify({
        "message": "Social Media Stats API",
        "version": "10.0",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram",
            "/api/facebook": "POST – Facebook (Pages & Groups)",
            "/api/health": "GET – health check",
        },
    })


# --------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
