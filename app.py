# --------------------------------------------------------------
# app.py  –  Social-Media Stats API (Flask + Telethon + FB Groups/Pages)
# --------------------------------------------------------------

import os
import re
import json
import asyncio
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


# ==================== TWITTER (unchanged) ====================
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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    for base in NITTER_INSTANCES:
        try:
            r = requests.get(f"{base}/{username}", headers=headers, timeout=15, allow_redirects=True)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, "html.parser")
            if "User not found" in r.text or "doesn't exist" in r.text: continue

            name = username
            name_elem = soup.find("a", class_="profile-card-fullname") or soup.find("div", class_="profile-card-fullname")
            if name_elem: name = name_elem.get_text(strip=True)

            bio = ""
            bio_elem = soup.find("p", class_="profile-bio")
            if bio_elem: bio = bio_elem.get_text(strip=True)

            tweets = following = followers = "0"
            stats = soup.find_all("span", class_="profile-stat-num")
            if len(stats) >= 3:
                tweets, following, followers = [s.get_text(strip=True) for s in stats[:3]]
            else:
                stat_list = soup.find("ul", class_="profile-statlist")
                if stat_list:
                    for li in stat_list.find_all("li"):
                        header = li.find("span", class_="profile-stat-header")
                        value  = li.find("span", class_="profile-stat-num")
                        if header and value:
                            h = header.get_text(strip=True).lower()
                            v = value.get_text(strip=True)
                            if "tweet" in h or "post" in h: tweets = v
                            elif "following" in h: following = v
                            elif "follower" in h: followers = v

            follower_cnt = parse_count(followers)

            avatar = f"https://unavatar.io/twitter/{username}"
            avatar_elem = soup.find("a", class_="profile-card-avatar")
            if avatar_elem:
                img = avatar_elem.find("img")
                if img and img.get("src"):
                    src = img.get("src")
                    avatar = src if src.startswith("http") else base + src

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
            print(f"Nitter {base} failed for {username}: {e}")
            continue

    # unavatar fallback
    try:
        if requests.head(f"https://unavatar.io/twitter/{username}", timeout=5).status_code == 200:
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
                "bio": "Profile exists but stats are currently unavailable.",
                "method": "unavatar",
            }
    except Exception:
        pass

    return {"success": False, "error": f"@{username} not found or temporarily unavailable"}


# ==================== INSTAGRAM – IMPROVED ====================
def extract_ig(url_or_at):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_instagram(username):
    username = username.lstrip("@")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.instagram.com/",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }

    # Try multiple approaches
    methods_tried = []

    # ---- Method 1: Direct Instagram with embedded JSON ----
    try:
        methods_tried.append("instagram_direct")
        r = requests.get(f"https://www.instagram.com/{username}/", headers=headers, timeout=20)
        
        if r.status_code == 200 and "login" not in r.url.lower():
            # Look for shared_data JSON
            shared_data_match = re.search(r'window\._sharedData\s*=\s*(\{.+?\});', r.text)
            if shared_data_match:
                shared_data = json.loads(shared_data_match.group(1))
                user = shared_data.get("entry_data", {}).get("ProfilePage", [{}])[0].get("graphql", {}).get("user", {})
                
                if user:
                    followers = user.get("edge_followed_by", {}).get("count", 0)
                    following = user.get("edge_follow", {}).get("count", 0)
                    posts = user.get("edge_owner_to_timeline_media", {}).get("count", 0)
                    full_name = user.get("full_name") or username
                    bio = user.get("biography") or ""
                    avatar = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or f"https://unavatar.io/instagram/{username}"
                    
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
                        "method": "instagram_shared_data",
                    }
            
            # Look for __additionalDataLoaded JSON
            additional_match = re.search(r'window\.__additionalDataLoaded\([\'"][^\'"]+[\'"],(\{.*?\})\);', r.text)
            if additional_match:
                payload = json.loads(additional_match.group(1))
                user = payload.get("graphql", {}).get("user", {})
                
                if user:
                    followers = user.get("edge_followed_by", {}).get("count", 0)
                    following = user.get("edge_follow", {}).get("count", 0)
                    posts = user.get("edge_owner_to_timeline_media", {}).get("count", 0)
                    full_name = user.get("full_name") or username
                    bio = user.get("biography") or ""
                    avatar = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or f"https://unavatar.io/instagram/{username}"
                    
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
                        "method": "instagram_additional_data",
                    }
            
            # Try meta tags fallback
            soup = BeautifulSoup(r.text, "html.parser")
            meta = soup.find("meta", property="og:description") or soup.find("meta", {"name": "description"})
            
            if meta:
                txt = meta.get("content", "")
                
                # Parse name
                name_match = re.search(r"^([^-(]+)", txt)
                name = name_match.group(1).strip() if name_match else username
                
                # Parse stats
                followers_match = re.search(r"([\d,.]+[KMB]?)\s*Followers?", txt, re.I)
                following_match = re.search(r"([\d,.]+[KMB]?)\s*Following", txt, re.I)
                posts_match = re.search(r"([\d,.]+[KMB]?)\s*Posts?", txt, re.I)
                
                # Parse bio
                bio = ""
                bio_match = re.search(r"-\s*(.+?)(?:\s*See Instagram photos|$)", txt)
                if bio_match:
                    bio = bio_match.group(1).strip()[:250]
                
                if followers_match or following_match or posts_match or name != username:
                    return {
                        "success": True,
                        "platform": "instagram",
                        "username": username,
                        "name": name,
                        "follower_count": format_count(parse_count(followers_match.group(1)), "followers") if followers_match else "Hidden",
                        "follower_count_exact": str(parse_count(followers_match.group(1))) if followers_match else "0",
                        "following_count": format_count(parse_count(following_match.group(1)), "following") if following_match else "Hidden",
                        "post_count": format_count(parse_count(posts_match.group(1)), "posts") if posts_match else "Hidden",
                        "profile_url": f"https://instagram.com/{username}",
                        "profile_image": f"https://unavatar.io/instagram/{username}",
                        "bio": bio,
                        "method": "instagram_meta",
                    }
    except Exception as e:
        print(f"Instagram direct method failed for {username}: {e}")

    # ---- Method 2: Picuki (Instagram viewer) ----
    try:
        methods_tried.append("picuki")
        r = requests.get(f"https://www.picuki.com/profile/{username}", headers=headers, timeout=15)
        
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Get name
            name_elem = soup.find("h1", class_="profile-name-top")
            name = name_elem.get_text(strip=True) if name_elem else username
            
            # Get stats
            stats_div = soup.find("div", class_="profile-stats")
            followers = following = posts = "Hidden"
            
            if stats_div:
                stat_items = stats_div.find_all("li")
                for item in stat_items:
                    value = item.find("span", class_="profile-stat-count")
                    label = item.find("span", class_="profile-stat-name")
                    if value and label:
                        v = value.get_text(strip=True)
                        l = label.get_text(strip=True).lower()
                        if "post" in l:
                            posts = format_count(parse_count(v), "posts")
                        elif "follower" in l:
                            followers = format_count(parse_count(v), "followers")
                        elif "following" in l:
                            following = format_count(parse_count(v), "following")
            
            # Get bio
            bio_elem = soup.find("div", class_="profile-description")
            bio = bio_elem.get_text(strip=True)[:250] if bio_elem else ""
            
            if followers != "Hidden" or following != "Hidden" or posts != "Hidden":
                return {
                    "success": True,
                    "platform": "instagram",
                    "username": username,
                    "name": name,
                    "follower_count": followers,
                    "follower_count_exact": str(parse_count(followers)) if followers != "Hidden" else "0",
                    "following_count": following,
                    "post_count": posts,
                    "profile_url": f"https://instagram.com/{username}",
                    "profile_image": f"https://unavatar.io/instagram/{username}",
                    "bio": bio,
                    "method": "picuki",
                }
    except Exception as e:
        print(f"Picuki method failed for {username}: {e}")

    # ---- Method 3: Dumpor (Instagram viewer) ----
    try:
        methods_tried.append("dumpor")
        r = requests.get(f"https://dumpor.com/v/{username}", headers=headers, timeout=15)
        
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            name_elem = soup.find("div", class_="user__title")
            name = name_elem.get_text(strip=True) if name_elem else username
            
            # Stats
            stats = soup.find_all("div", class_="user__info-stats")
            followers = following = posts = "Hidden"
            
            for stat in stats:
                text = stat.get_text(strip=True)
                if "followers" in text.lower():
                    num = re.search(r"([\d,.]+[KMB]?)", text)
                    if num:
                        followers = format_count(parse_count(num.group(1)), "followers")
                elif "following" in text.lower():
                    num = re.search(r"([\d,.]+[KMB]?)", text)
                    if num:
                        following = format_count(parse_count(num.group(1)), "following")
                elif "posts" in text.lower() or "post" in text.lower():
                    num = re.search(r"([\d,.]+[KMB]?)", text)
                    if num:
                        posts = format_count(parse_count(num.group(1)), "posts")
            
            bio_elem = soup.find("div", class_="user__info-desc")
            bio = bio_elem.get_text(strip=True)[:250] if bio_elem else ""
            
            if followers != "Hidden" or following != "Hidden" or posts != "Hidden":
                return {
                    "success": True,
                    "platform": "instagram",
                    "username": username,
                    "name": name,
                    "follower_count": followers,
                    "follower_count_exact": str(parse_count(followers)) if followers != "Hidden" else "0",
                    "following_count": following,
                    "post_count": posts,
                    "profile_url": f"https://instagram.com/{username}",
                    "profile_image": f"https://unavatar.io/instagram/{username}",
                    "bio": bio,
                    "method": "dumpor",
                }
    except Exception as e:
        print(f"Dumpor method failed for {username}: {e}")

    # ---- Method 4: Unavatar existence check ----
    try:
        methods_tried.append("unavatar")
        if requests.head(f"https://unavatar.io/instagram/{username}", timeout=5).status_code == 200:
            return {
                "success": True,
                "platform": "instagram",
                "username": username,
                "name": username.replace("_", " ").replace(".", " ").title(),
                "follower_count": "Hidden",
                "follower_count_exact": "0",
                "following_count": "Hidden",
                "post_count": "Hidden",
                "profile_url": f"https://instagram.com/{username}",
                "profile_image": f"https://unavatar.io/instagram/{username}",
                "bio": "Profile exists but requires login to view stats. Instagram has restricted public access.",
                "method": "unavatar",
                "note": "Due to Instagram's privacy changes, stats may not be available without authentication."
            }
    except Exception as e:
        print(f"Unavatar check failed for {username}: {e}")

    return {
        "success": False, 
        "error": f"@{username} not found or is private. Instagram has restricted public data access.",
        "methods_tried": methods_tried
    }


# ==================== TIKTOK (unchanged) ====================
def extract_tt(url_or_at):
    m = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_tiktok(username):
    username = username.lstrip("@")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
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
                    "bio": "Profile exists but stats unavailable",
                    "method": "unavatar",
                }
        except Exception:
            pass
    return {"success": False, "error": f"@{username} not reachable"}


# ==================== TELEGRAM (unchanged) ====================
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
        avatar = f"https://unavatar.io/telegram/{username}"

        if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
            full = await client(GetFullChannelRequest(entity))
            typ = ("Channel" if entity.broadcast else "Supergroup" if entity.megagroup else "Group")
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


# ==================== FACEBOOK (unchanged) ====================
def extract_fb(url_or_at):
    url_lower = url_or_at.lower()
    # groups with numeric ID
    m = re.search(r"facebook\.com/groups/(\d+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    # groups with slug
    m = re.search(r"facebook\.com/groups/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    # pages
    m = re.search(r"(?:facebook\.com|fb\.com)/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m:
        ident = m.group(1)
        if ident.lower() not in {"profile.php", "watch", "marketplace", "groups", "sharer", "share", "pages", "people"}:
            return ident, "page"
    # handle only
    if "facebook.com" not in url_lower and "fb.com" not in url_lower:
        m = re.search(r"^@?([A-Za-z0-9.\-_]+)$", url_or_at)
        if m: return m.group(1), "page"
    return None, None


def scrape_facebook(identifier, entity_type="page"):
    base_url = f"https://www.facebook.com/{identifier}" if entity_type == "page" else f"https://www.facebook.com/groups/{identifier}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    # ---------- 1. Desktop scrape ----------
    try:
        r = requests.get(base_url, headers=headers, timeout=20, allow_redirects=True)
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")
            text = r.text

            # name
            title = soup.find("title")
            name = title.get_text(strip=True) if title else ""
            name = re.sub(r"\s*[-|·]\s*Facebook$", "", name, flags=re.I)
            name = re.sub(r"^(Log into|Log In|Sign Up|Meta).*", "", name, flags=re.I).strip()
            if not name or name.lower() in {"facebook", "log in", "meta"}:
                name = identifier.replace("_", " ").replace("-", " ").title()

            is_group = entity_type == "group" or "/groups/" in r.url or "group" in name.lower()

            # count
            count = "Hidden"
            count_exact = "0"
            count_label = ""
            patterns = [
                r'(\d{1,3}(?:,\d{3})*)\s*(?:members?|followers?|likes?)',
                r'(\d+\.?\d*[KMB])\s*(?:members?|followers?|likes?)',
                r'"member_count":\s*(\d+)',
                r'"follower_count":\s*(\d+)',
                r'"page_likers":\s*(\d+)',
            ]
            for pat in patterns:
                m = re.search(pat, text, re.I)
                if m:
                    parsed = parse_count(m.group(1).replace(",", ""))
                    if parsed > 0:
                        count_exact = str(parsed)
                        if is_group or "member" in pat.lower():
                            count = format_count(count_exact, "members")
                            count_label = "members"
                        elif "follow" in pat.lower():
                            count = format_count(count_exact, "followers")
                            count_label = "followers"
                        else:
                            count = format_count(count_exact, "likes")
                            count_label = "likes"
                        break

            # bio / description
            bio = ""
            meta = soup.find("meta", {"property": "og:description"}) or soup.find("meta", {"name": "description"})
            if meta:
                bio = meta.get("content", "")
                bio = re.sub(r"^\S+?\s*[·|]\s*", "", bio)
                bio = re.sub(r"\s*Log In.*", "", bio, flags=re.I)
                bio = re.sub(r"\s*See more.*", "", bio, flags=re.I)
                bio = re.sub(r"\s*\d+\s*(?:members?|followers?|likes?).*", "", bio, flags=re.I)
                bio = bio.strip()[:250]

            # avatar
            avatar = f"https://unavatar.io/facebook/{identifier}"
            og_img = soup.find("meta", {"property": "og:image"})
            if og_img and og_img.get("content"):
                avatar = og_img["content"]

            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": name,
                "type": "Group" if is_group else "Page",
                "count": count,
                "count_exact": count_exact,
                "count_label": count_label,
                "profile_url": base_url,
                "profile_image": avatar,
                "bio": bio or "Details may require login",
                "method": "facebook_desktop",
            }
    except Exception as e:
        print(f"FB desktop error {identifier}: {e}")

    # ---------- 2. Mobile fallback ----------
    try:
        mobile_url = base_url.replace("www.facebook.com", "mbasic.facebook.com")
        r = requests.get(mobile_url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            name = identifier.replace("_", " ").title()
            h = soup.find("h3") or soup.find("h1")
            if h: name = h.get_text(strip=True)

            count = "Hidden"
            count_label = ""
            m = re.search(r'(\d{1,3}(?:,\d{3})*)\s*(members?|followers?|likes?)', r.text, re.I)
            if m:
                parsed = parse_count(m.group(1))
                if parsed > 0:
                    label = m.group(2).lower()
                    if "member" in label:
                        count = format_count(parsed, "members")
                        count_label = "members"
                    elif "follow" in label:
                        count = format_count(parsed, "followers")
                        count_label = "followers"
                    else:
                        count = format_count(parsed, "likes")
                        count_label = "likes"

            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": name,
                "type": "Group" if entity_type == "group" else "Page",
                "count": count,
                "count_exact": str(parsed) if count != "Hidden" else "0",
                "count_label": count_label,
                "profile_url": base_url,
                "profile_image": f"https://unavatar.io/facebook/{identifier}",
                "bio": "Limited data from mobile view",
                "method": "facebook_mobile",
            }
    except Exception as e:
        print(f"FB mobile error {identifier}: {e}")

    # ---------- 3. Unavatar existence ----------
    try:
        if requests.head(f"https://unavatar.io/facebook/{identifier}", timeout=5).status_code == 200:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": identifier.replace("_", " ").title(),
                "type": "Page" if entity_type == "page" else "Group",
                "count": "Hidden",
                "count_exact": "0",
                "count_label": "",
                "profile_url": base_url,
                "profile_image": f"https://unavatar.io/facebook/{identifier}",
                "bio": "Profile exists – login required for stats",
                "method": "unavatar",
            }
    except Exception:
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

    # YouTube
    if "youtube.com" in url or "youtu.be" in url:
        if not YOUTUBE_API_KEY:
            return jsonify({"success": False, "error": "YouTube API key missing"}), 500
        cid, typ = extract_yt(url)
        if not cid:
            return jsonify({"success": False, "error": "Invalid YouTube URL"}), 400
        return jsonify(yt_by_id(cid) if typ == "id" else yt_by_handle(cid))

    # Twitter / X
    if "twitter.com" in url or "x.com" in url:
        user = extract_tw(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid Twitter handle"}), 400
        return jsonify(scrape_twitter(user))

    # Instagram
    if "instagram.com" in url:
        user = extract_ig(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid Instagram handle"}), 400
        return jsonify(scrape_instagram(user))

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
        ident, etype = extract_fb(url)
        if not ident:
            return jsonify({"success": False, "error": "Invalid Facebook URL"}), 400
        return jsonify(scrape_facebook(ident, etype))

    # Handle-only (try all platforms)
    if url.startswith("@"):
        user = url.lstrip("@")
        for scraper in (scrape_twitter, scrape_instagram, scrape_tiktok, scrape_telegram,
                        lambda u: scrape_facebook(u, "page")):
            res = scraper(user)
            if res.get("success"):
                return jsonify(res)

    return jsonify({"success": False, "error": "Unsupported platform or invalid URL"}), 400


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
        "supported": ["YouTube", "Twitter/X", "Instagram", "TikTok", "Telegram", "Facebook (Pages & Groups)"],
    })

@app.route("/api/info", methods=["GET"])
def info():
    return jsonify({
        "message": "Social Media Stats API",
        "version": "12.1",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/instagram": "POST – Instagram (improved)",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram",
            "/api/facebook": "POST – Facebook (Pages & Groups)",
            "/api/health": "GET – health check",
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
