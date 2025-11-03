# --------------------------------------------------------------
# app.py – Social-Media Stats API v18 (Facebook & Twitter ENHANCED)
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
    if not txt:
        return 0
    txt = str(txt).strip()
    txt = re.sub(r"[^\dKMGB.]", "", txt.upper())
    if not txt:
        return 0
    if "K" in txt: 
        return int(float(txt.replace("K", "")) * 1_000)
    if "M" in txt: 
        return int(float(txt.replace("M", "")) * 1_000_000)
    if "B" in txt: 
        return int(float(txt.replace("B", "")) * 1_000_000_000)
    try:
        return int(float(txt))
    except:
        return 0


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


# ==================== TWITTER (ENHANCED) ====================
NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://nitter.cz",
    "https://nitter.1d4.us",
    "https://nitter.unixfox.eu",
    "https://nitter.moomoo.me",
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
        "Upgrade-Insecure-Requests": "1",
    }

    # Try multiple Nitter instances
    for base in NITTER_INSTANCES:
        try:
            print(f"Trying {base} for @{username}...")
            r = requests.get(f"{base}/{username}", headers=headers, timeout=20, allow_redirects=True)
            
            if r.status_code != 200:
                print(f"  Status {r.status_code}, skipping")
                continue
                
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Check for error messages
            if any(err in r.text.lower() for err in ["user not found", "doesn't exist", "suspended", "banned"]):
                print(f"  User not found on {base}")
                continue

            # Extract name
            name = username
            name_elem = soup.find("a", class_="profile-card-fullname") or soup.find("div", class_="profile-card-fullname")
            if name_elem:
                name = name_elem.get_text(strip=True)

            # Extract bio/description
            bio = ""
            bio_elem = soup.find("p", class_="profile-bio") or soup.find("div", class_="profile-bio")
            if bio_elem:
                bio = bio_elem.get_text(strip=True)

            # Extract stats
            tweets = following = followers = "0"
            
            # Method 1: profile-stat-num spans
            stats = soup.find_all("span", class_="profile-stat-num")
            if len(stats) >= 3:
                tweets, following, followers = [s.get_text(strip=True) for s in stats[:3]]
            else:
                # Method 2: profile-statlist
                stat_list = soup.find("ul", class_="profile-statlist")
                if stat_list:
                    for li in stat_list.find_all("li"):
                        header = li.find("span", class_="profile-stat-header")
                        value = li.find("span", class_="profile-stat-num")
                        if header and value:
                            h = header.get_text(strip=True).lower()
                            v = value.get_text(strip=True)
                            if "tweet" in h or "post" in h:
                                tweets = v
                            elif "following" in h:
                                following = v
                            elif "follower" in h:
                                followers = v

            # Parse follower count
            follower_cnt = parse_count(followers)

            # Extract avatar
            avatar = f"https://unavatar.io/twitter/{username}"
            avatar_elem = soup.find("a", class_="profile-card-avatar")
            if avatar_elem:
                img = avatar_elem.find("img")
                if img and img.get("src"):
                    src = img.get("src")
                    if src.startswith("http"):
                        avatar = src
                    elif src.startswith("/"):
                        avatar = base + src

            # Validate we got meaningful data
            if follower_cnt > 0 or name != username or bio or tweets != "0":
                print(f"  ✓ Success on {base}!")
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
            else:
                print(f"  No valid data on {base}")
                
        except Exception as e:
            print(f"  Error on {base}: {e}")
            continue

    # Fallback: Check unavatar
    try:
        unavatar_check = requests.head(f"https://unavatar.io/twitter/{username}", timeout=5)
        if unavatar_check.status_code == 200:
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

    return {"success": False, "error": f"@{username} not found or temporarily unavailable on all Nitter instances"}


# ==================== INSTAGRAM ====================
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
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.instagram.com/",
        "x-ig-app-id": "936619743392459",
        "x-asbd-id": "129477",
        "x-ig-www-claim": "0",
        "x-requested-with": "XMLHttpRequest",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

    # ---- METHOD 1: Official API (HD avatar) ----
    try:
        api_url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
        r = requests.get(api_url, headers=headers, timeout=20)
        if r.status_code == 200:
            data = r.json()
            user = data.get("data", {}).get("user", {})
            if user:
                avatar = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or f"https://unavatar.io/instagram/{username}"
                followers = user.get("edge_followed_by", {}).get("count", 0)
                following = user.get("edge_follow", {}).get("count", 0)
                posts = user.get("edge_owner_to_timeline_media", {}).get("count", 0)
                full_name = user.get("full_name") or username
                bio = user.get("biography") or ""
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
                    "is_verified": user.get("is_verified", False),
                    "is_private": user.get("is_private", False),
                    "method": "instagram_api",
                }
    except Exception as e:
        print(f"Instagram API failed: {e}")

    # ---- METHOD 2: Public page JSON ----
    try:
        page = requests.get(f"https://www.instagram.com/{username}/", headers=headers, timeout=15)
        if page.status_code == 200:
            m = re.search(r'window\._sharedData = ({.+?});\s*</script>', page.text)
            if m:
                shared = json.loads(m.group(1))
                entry = shared.get("entry_data", {}).get("ProfilePage", [{}])[0]
                user = entry.get("graphql", {}).get("user", {})
                if user:
                    avatar = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or f"https://unavatar.io/instagram/{username}"
                    followers = user.get("edge_followed_by", {}).get("count", 0)
                    following = user.get("edge_follow", {}).get("count", 0)
                    posts = user.get("edge_owner_to_timeline_media", {}).get("count", 0)
                    full_name = user.get("full_name") or username
                    bio = user.get("biography") or ""
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
                        "method": "instagram_sharedData",
                    }
    except Exception as e:
        print(f"Instagram page JSON failed: {e}")

    # ---- METHOD 3: og:image + meta description fallback ----
    try:
        r = requests.get(f"https://www.instagram.com/{username}/", headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            avatar = f"https://unavatar.io/instagram/{username}"
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                avatar = og_img["content"]
            
            pic_match = re.search(r'"profile_pic_url":"(https://[^"]+)"', r.text)
            if pic_match:
                avatar = pic_match.group(1).replace("\\u0026", "&")
            
            pic_hd_match = re.search(r'"profile_pic_url_hd":"(https://[^"]+)"', r.text)
            if pic_hd_match:
                avatar = pic_hd_match.group(1).replace("\\u0026", "&")

            name = username
            followers = following = posts = 0
            bio = ""
            meta = soup.find("meta", property="og:description")
            if meta:
                txt = meta.get("content", "")
                name_match = re.search(r"^([^-(]+)", txt)
                if name_match: name = name_match.group(1).strip()
                followers_match = re.search(r"([\d,.]+[KMB]?)\s*Followers?", txt, re.I)
                following_match = re.search(r"([\d,.]+[KMB]?)\s*Following", txt, re.I)
                posts_match = re.search(r"([\d,.]+[KMB]?)\s*Posts?", txt, re.I)
                bio_match = re.search(r"-\s*(.+?)(?:\s*See Instagram|$)", txt)
                if bio_match: bio = bio_match.group(1).strip()[:250]
                if followers_match: followers = parse_count(followers_match.group(1))
                if following_match: following = parse_count(following_match.group(1))
                if posts_match: posts = parse_count(posts_match.group(1))

            if followers or posts:
                return {
                    "success": True,
                    "platform": "instagram",
                    "username": username,
                    "name": name,
                    "follower_count": format_count(followers, "followers"),
                    "follower_count_exact": str(followers),
                    "following_count": format_count(following, "following"),
                    "post_count": format_count(posts, "posts"),
                    "profile_url": f"https://instagram.com/{username}",
                    "profile_image": avatar,
                    "bio": bio,
                    "method": "instagram_web",
                }
    except Exception as e:
        print(f"Instagram web scrape failed: {e}")

    # ---- FINAL FALLBACK: unavatar ----
    try:
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
                "bio": "Profile exists. Instagram requires login for detailed stats.",
                "method": "unavatar",
            }
    except:
        pass

    return {"success": False, "error": f"Cannot access @{username}. Profile may be private or blocked."}


# ==================== TIKTOK ====================
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

        try:
            photo_bytes = await client.download_profile_photo(entity, file=bytes)
            if photo_bytes:
                b64 = base64.b64encode(photo_bytes).decode()
                avatar = f"data:image/jpeg;base64,{b64}"
            else:
                raise ValueError("No photo")
        except Exception:
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


# ==================== FACEBOOK (SUPER ENHANCED) ====================
def extract_fb(url_or_at):
    url_lower = url_or_at.lower()
    
    # Personal profile detection
    if re.search(r"facebook\.com/profile\.php", url_or_at, re.I):
        return None, "profile"
    if re.search(r"facebook\.com/people/", url_or_at, re.I):
        return None, "profile"
    
    # Known personal profiles
    personal_profiles = ['zuck', 'zuckerberg', 'sherylsandberg', 'billgates']
    identifier_check = re.search(r"(?:facebook\.com|fb\.com)/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if identifier_check and identifier_check.group(1).lower() in personal_profiles:
        return None, "profile"
    
    # Group detection
    m = re.search(r"facebook\.com/groups/(\d+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    m = re.search(r"facebook\.com/groups/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    
    # Page detection
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
    """Enhanced Facebook scraper with multiple strategies"""
    
    if entity_type == "profile":
        return {
            "success": False,
            "error": "❌ Personal Facebook profiles cannot be scraped. Only Pages and Groups are supported!"
        }

    base_url = f"https://www.facebook.com/{identifier}" if entity_type == "page" else f"https://www.facebook.com/groups/{identifier}"
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    })

    # ========== STRATEGY 1: Mobile Site (mbasic.facebook.com) ==========
    try:
        print(f"[Facebook] Trying mbasic for {identifier}...")
        mbasic_url = f"https://mbasic.facebook.com/{identifier}" if entity_type == "page" else f"https://mbasic.facebook.com/groups/{identifier}"
        r = session.get(mbasic_url, timeout=25, allow_redirects=True)
        
        # Check if redirected to personal profile
        if "/profile.php" in r.url or "/people/" in r.url:
            return {"success": False, "error": "❌ This is a personal profile, not a Page/Group"}
        
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")

            # Extract name
            name = identifier.replace("_", " ").replace("-", " ").title()
            title_tag = soup.find("title")
            if title_tag:
                title_text = title_tag.get_text(strip=True)
                name = title_text.split(" - ")[0].split("|")[0].split("(")[0].strip()

            # Extract avatar with priority order
            avatar = f"https://unavatar.io/facebook/{identifier}"
            
            # Look for profile/page image
            for img in soup.find_all("img", limit=15):
                src = img.get("src", "")
                alt = img.get("alt", "").lower()
                
                # Priority 1: Explicit profile/logo images
                if ("scontent" in src or "fbcdn" in src) and any(kw in alt for kw in ["profile", "logo", name.lower()]):
                    avatar = src
                    break
                # Priority 2: Any fbcdn image near the top
                elif "fbcdn" in src and "scontent" in src:
                    avatar = src
                    break

            # Determine if group
            is_group = entity_type == "group" or "/groups/" in r.url
            count_label = "members" if is_group else "followers"
            
            # Extract follower/member count - ENHANCED PATTERNS
            count = "Hidden"
            count_exact = "0"
            
            # Pattern 1: Direct count in visible text
            visible_text = soup.get_text()
            count_patterns = [
                r'([\d,]+)\s+people\s+(?:like|follow)\s+this',
                r'([\d,]+)\s+likes',
                r'([\d,]+)\s+followers?',
                r'([\d,]+)\s+members?',
                r'([\d,.]+[KMB]?)\s+(?:follower|member|like)s?',
            ]
            
            for pattern in count_patterns:
                matches = re.findall(pattern, visible_text, re.I)
                for match in matches:
                    parsed = parse_count(match.replace(",", ""))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        count = format_count(parsed, count_label)
                        print(f"  Found count: {count}")
                        break
                if count != "Hidden":
                    break
            
            # Pattern 2: Check specific divs/sections
            if count == "Hidden":
                for div in soup.find_all("div", limit=50):
                    div_text = div.get_text(strip=True)
                    for pattern in count_patterns:
                        match = re.search(pattern, div_text, re.I)
                        if match:
                            parsed = parse_count(match.group(1).replace(",", ""))
                            if parsed >= 10:
                                count_exact = str(parsed)
                                count = format_count(parsed, count_label)
                                print(f"  Found count in div: {count}")
                                break
                    if count != "Hidden":
                        break

            # Extract description/bio - ENHANCED
            bio = ""
            
            # Method 1: About section
            about_div = soup.find("div", {"id": "about_basics"})
            if about_div:
                bio = about_div.get_text(strip=True)[:300]
            
            # Method 2: Look for description in any div
            if not bio:
                for div in soup.find_all("div", limit=30):
                    div_text = div.get_text(strip=True)
                    if len(div_text) > 50 and len(div_text) < 500:
                        # Skip if it looks like navigation or counts
                        if not any(skip in div_text.lower() for skip in ["photos", "videos", "posts", "about", "community", "like", "follow", "share"]):
                            bio = div_text[:300]
                            break
            
            # Method 3: Meta description
            if not bio:
                meta_desc = soup.find("meta", {"name": "description"})
                if meta_desc:
                    bio = meta_desc.get("content", "")[:300]
            
            # Extract count from bio if not found yet
            if count == "Hidden" and bio:
                bio_patterns = [
                    r'([\d,]+)\s+likes',
                    r'([\d,]+)\s+followers',
                    r'([\d,]+)\s+members',
                ]
                for pat in bio_patterns:
                    bio_match = re.search(pat, bio, re.I)
                    if bio_match:
                        parsed = parse_count(bio_match.group(1).replace(",", ""))
                        if parsed >= 10:
                            count_exact = str(parsed)
                            count = format_count(parsed, count_label)
                            break

            print(f"  mbasic result: name={name}, count={count}, bio_len={len(bio)}")
            
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
                "bio": bio or "Facebook Page/Group",
                "method": "facebook_mbasic",
            }
    except Exception as e:
        print(f"  mbasic failed: {e}")

    # ========== STRATEGY 2: Desktop Site ==========
    try:
        print(f"[Facebook] Trying desktop site for {identifier}...")
        r = session.get(base_url, timeout=25, allow_redirects=True)
        
        if "/profile.php" in r.url or "/people/" in r.url:
            return {"success": False, "error": "❌ This is a personal profile"}
        
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")
            html_content = r.text
            
            # Extract name
            name = identifier.replace("_", " ").replace("-", " ").title()
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                name = re.sub(r"\s*[-|]\s*Facebook$", "", og_title["content"], flags=re.I).strip()

            # Extract avatar
            avatar = f"https://unavatar.io/facebook/{identifier}"
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                avatar = og_img["content"]

            is_group = entity_type == "group" or "/groups/" in r.url
            count_label = "members" if is_group else "followers"
            count = "Hidden"
            count_exact = "0"
            
            # ENHANCED: Extract from JSON-LD data
            json_ld_scripts = soup.find_all("script", type="application/ld+json")
            for script in json_ld_scripts:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict):
                        # Check for interactionStatistic
                        if "interactionStatistic" in data:
                            for stat in data["interactionStatistic"]:
                                if stat.get("interactionType") == "http://schema.org/FollowAction":
                                    count_val = stat.get("userInteractionCount", 0)
                                    if count_val > 10:
                                        count_exact = str(count_val)
                                        count = format_count(count_val, count_label)
                                        print(f"  Found in JSON-LD: {count}")
                                        break
                except:
                    pass
            
            # Extract from inline JSON data
            if count == "Hidden":
                json_patterns = [
                    r'"follower_count["\']?\s*:\s*(\d+)',
                    r'"member_count["\']?\s*:\s*(\d+)',
                    r'"fan_count["\']?\s*:\s*(\d+)',
                    r'"likes["\']?\s*:\s*(\d+)',
                    r'followerCount["\']?\s*:\s*(\d+)',
                    r'memberCount["\']?\s*:\s*(\d+)',
                ]
                
                for pattern in json_patterns:
                    matches = re.findall(pattern, html_content)
                    for match in matches:
                        parsed = int(match)
                        if parsed >= 10:
                            count_exact = str(parsed)
                            count = format_count(parsed, count_label)
                            print(f"  Found in JSON: {count}")
                            break
                    if count != "Hidden":
                        break
            
            # Extract from visible text
            if count == "Hidden":
                visible_patterns = [
                    r'([\d,]+)\s+(?:people\s+)?(?:like|follow)\s+this',
                    r'([\d,.]+[KMB]?)\s+likes',
                    r'([\d,.]+[KMB]?)\s+followers?',
                    r'([\d,.]+[KMB]?)\s+members?',
                ]
                
                page_text = soup.get_text()
                for pattern in visible_patterns:
                    match = re.search(pattern, page_text, re.I)
                    if match:
                        parsed = parse_count(match.group(1).replace(",", ""))
                        if parsed >= 10:
                            count_exact = str(parsed)
                            count = format_count(parsed, count_label)
                            print(f"  Found in text: {count}")
                            break

            # Extract description - ENHANCED
            bio = ""
            
            # Method 1: og:description
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                bio = og_desc.get("content", "")[:300]
            
            # Method 2: meta description
            if not bio:
                meta_desc = soup.find("meta", {"name": "description"})
                if meta_desc:
                    bio = meta_desc.get("content", "")[:300]
            
            # Method 3: Look for about/description in JSON
            if not bio:
                desc_patterns = [
                    r'"about["\']?\s*:\s*["\']([^"\']{20,300})["\']',
                    r'"description["\']?\s*:\s*["\']([^"\']{20,300})["\']',
                ]
                for pattern in desc_patterns:
                    match = re.search(pattern, html_content)
                    if match:
                        bio = match.group(1)[:300]
                        break
            
            # Try to extract count from bio if still hidden
            if count == "Hidden" and bio:
                bio_patterns = [
                    r'([\d,]+)\s+(?:likes|followers|members)',
                ]
                for pattern in bio_patterns:
                    match = re.search(pattern, bio, re.I)
                    if match:
                        parsed = parse_count(match.group(1).replace(",", ""))
                        if parsed >= 10:
                            count_exact = str(parsed)
                            count = format_count(parsed, count_label)
                            break
            
            print(f"  desktop result: name={name}, count={count}, bio_len={len(bio)}")

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
                "bio": bio or "Facebook Page/Group",
                "method": "facebook_desktop",
            }
    except Exception as e:
        print(f"  desktop failed: {e}")

    # ========== STRATEGY 3: Touch Site (touch.facebook.com) ==========
    try:
        print(f"[Facebook] Trying touch site for {identifier}...")
        touch_url = f"https://touch.facebook.com/{identifier}" if entity_type == "page" else f"https://touch.facebook.com/groups/{identifier}"
        r = session.get(touch_url, timeout=25, allow_redirects=True)
        
        if r.status_code == 200 and "login" not in r.url.lower() and "/profile.php" not in r.url:
            soup = BeautifulSoup(r.text, "html.parser")
            
            name = identifier.replace("_", " ").replace("-", " ").title()
            title_tag = soup.find("title")
            if title_tag:
                name = title_tag.get_text(strip=True).split("|")[0].split("-")[0].strip()
            
            avatar = f"https://unavatar.io/facebook/{identifier}"
            for img in soup.find_all("img", limit=10):
                src = img.get("src", "")
                if "fbcdn" in src and "scontent" in src:
                    avatar = src
                    break
            
            is_group = entity_type == "group" or "/groups/" in r.url
            count_label = "members" if is_group else "followers"
            count = "Hidden"
            count_exact = "0"
            
            text_content = soup.get_text()
            patterns = [
                r'([\d,]+)\s+(?:likes|followers|members)',
                r'([\d,.]+[KMB]?)\s+people',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text_content, re.I)
                if match:
                    parsed = parse_count(match.group(1).replace(",", ""))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        count = format_count(parsed, count_label)
                        break
            
            bio = ""
            meta_desc = soup.find("meta", property="og:description")
            if meta_desc:
                bio = meta_desc.get("content", "")[:300]
            
            print(f"  touch result: name={name}, count={count}")
            
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
                "bio": bio or "Facebook Page/Group",
                "method": "facebook_touch",
            }
    except Exception as e:
        print(f"  touch failed: {e}")

    # ========== FALLBACK: Unavatar ==========
    try:
        print(f"[Facebook] Trying unavatar for {identifier}...")
        unavatar_check = requests.head(f"https://unavatar.io/facebook/{identifier}", timeout=5)
        if unavatar_check.status_code == 200:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": identifier.replace("_", " ").replace("-", " ").title(),
                "type": "Group" if entity_type == "group" else "Page",
                "count": "Hidden",
                "count_exact": "0",
                "count_label": "members" if entity_type == "group" else "followers",
                "profile_url": base_url,
                "profile_image": f"https://unavatar.io/facebook/{identifier}",
                "bio": "Profile exists. Stats require Facebook login.",
                "method": "unavatar",
            }
    except:
        pass

    return {"success": False, "error": f"Cannot access {identifier}. It may be private or doesn't exist."}


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
        if etype == "profile":
            return jsonify({"success": False, "error": "❌ Personal Facebook profiles cannot be scraped. Only Pages and Groups!"}), 400
        if not ident: return jsonify({"success": False, "error": "Invalid Facebook URL"}), 400
        return jsonify(scrape_facebook(ident, etype))

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
    if etype == "profile":
        return jsonify({"success": False, "error": "❌ Personal Facebook profiles cannot be scraped!"}), 400
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
        "version": "18.0 - Twitter & Facebook SUPER ENHANCED",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X (enhanced with multiple Nitter instances)",
            "/api/instagram": "POST – Instagram (HD profile pics)",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram",
            "/api/facebook": "POST – Facebook Pages & Groups (3-strategy approach)",
            "/api/health": "GET – health check",
        },
        "improvements": {
            "twitter": "Added more Nitter instances + enhanced stat extraction + better error handling",
            "facebook": "Triple strategy (mbasic/desktop/touch) + JSON-LD parsing + bio extraction + exact follower counts"
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
