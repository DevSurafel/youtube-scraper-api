# --------------------------------------------------------------
# app.py – Social-Media Stats API (Instagram & Facebook FIXED)
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
                        value = li.find("span", class_="profile-stat-num")
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


# ==================== INSTAGRAM – ENHANCED WITH BETTER PROFILE PIC FALLBACKS ====================
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

    # ---- METHOD 1: Try imginn.com for profile pic + basic info ----
    try:
        r = requests.get(f"https://imginn.com/{username}/", headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Get profile image
            avatar = None
            img_elem = soup.find("img", class_="avatar") or soup.find("img", alt=re.compile(username, re.I))
            if img_elem and img_elem.get("src"):
                avatar = img_elem["src"]
            
            # Get name
            name = username
            name_elem = soup.find("h1") or soup.find("div", class_="fullname")
            if name_elem:
                name = name_elem.get_text(strip=True)
            
            # Get stats
            followers = following = posts = 0
            stats_list = soup.find_all("span", class_="num")
            if len(stats_list) >= 3:
                posts = parse_count(stats_list[0].get_text(strip=True))
                followers = parse_count(stats_list[1].get_text(strip=True))
                following = parse_count(stats_list[2].get_text(strip=True))
            
            # Get bio
            bio = ""
            bio_elem = soup.find("div", class_="bio") or soup.find("div", class_="description")
            if bio_elem:
                bio = bio_elem.get_text(strip=True)[:250]
            
            if avatar or followers > 0:
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
                    "profile_image": avatar or f"https://unavatar.io/instagram/{username}",
                    "bio": bio,
                    "method": "imginn",
                }
    except Exception as e:
        print(f"imginn failed: {e}")

    # ---- METHOD 2: Try picuki.com ----
    try:
        r = requests.get(f"https://www.picuki.com/profile/{username}", headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            avatar = None
            img = soup.find("img", class_="profile-avatar")
            if img and img.get("src"):
                avatar = img["src"]
            
            name = username
            name_elem = soup.find("h1", class_="profile-name-top")
            if name_elem:
                name = name_elem.get_text(strip=True)
            
            followers = following = posts = 0
            stats = soup.find_all("div", class_="profile-stat-count")
            if len(stats) >= 3:
                posts = parse_count(stats[0].get_text(strip=True))
                followers = parse_count(stats[1].get_text(strip=True))
                following = parse_count(stats[2].get_text(strip=True))
            
            bio = ""
            bio_elem = soup.find("div", class_="profile-description")
            if bio_elem:
                bio = bio_elem.get_text(strip=True)[:250]
            
            if avatar or followers > 0:
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
                    "profile_image": avatar or f"https://unavatar.io/instagram/{username}",
                    "bio": bio,
                    "method": "picuki",
                }
    except Exception as e:
        print(f"picuki failed: {e}")

    # ---- METHOD 3: Official Instagram with enhanced scraping ----
    try:
        page = requests.get(f"https://www.instagram.com/{username}/", headers=headers, timeout=15)
        if page.status_code == 200:
            soup = BeautifulSoup(page.text, "html.parser")
            
            # Try multiple methods to get avatar
            avatar = f"https://unavatar.io/instagram/{username}"
            
            # Method A: og:image
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                avatar = og_img["content"]
            
            # Method B: JSON-LD structured data
            json_ld = soup.find("script", type="application/ld+json")
            if json_ld:
                try:
                    ld_data = json.loads(json_ld.string)
                    if "image" in ld_data:
                        avatar = ld_data["image"]
                except:
                    pass
            
            # Extract stats from og:description
            name = username
            followers = following = posts = 0
            bio = ""
            meta = soup.find("meta", property="og:description")
            if meta:
                txt = meta.get("content", "")
                name_match = re.search(r"^([^-(]+)", txt)
                if name_match: 
                    name = name_match.group(1).strip()
                
                followers_match = re.search(r"([\d,.]+[KMB]?)\s*Followers?", txt, re.I)
                following_match = re.search(r"([\d,.]+[KMB]?)\s*Following", txt, re.I)
                posts_match = re.search(r"([\d,.]+[KMB]?)\s*Posts?", txt, re.I)
                bio_match = re.search(r"-\s*(.+?)(?:\s*See Instagram|$)", txt)
                
                if bio_match: 
                    bio = bio_match.group(1).strip()[:250]
                if followers_match: 
                    followers = parse_count(followers_match.group(1))
                if following_match: 
                    following = parse_count(following_match.group(1))
                if posts_match: 
                    posts = parse_count(posts_match.group(1))

            if followers or posts or avatar != f"https://unavatar.io/instagram/{username}":
                return {
                    "success": True,
                    "platform": "instagram",
                    "username": username,
                    "name": name,
                    "follower_count": format_count(followers, "followers") if followers else "Hidden",
                    "follower_count_exact": str(followers),
                    "following_count": format_count(following, "following") if following else "Hidden",
                    "post_count": format_count(posts, "posts") if posts else "Hidden",
                    "profile_url": f"https://instagram.com/{username}",
                    "profile_image": avatar,
                    "bio": bio,
                    "method": "instagram_web",
                }
    except Exception as e:
        print(f"Instagram web scrape failed: {e}")

    # ---- FINAL FALLBACK: unavatar (always works) ----
    try:
        unavatar_url = f"https://unavatar.io/instagram/{username}"
        check = requests.head(unavatar_url, timeout=5)
        if check.status_code == 200:
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
                "profile_image": unavatar_url,
                "bio": "Profile exists. Instagram requires login for detailed stats.",
                "method": "unavatar",
            }
    except:
        pass

    return {"success": False, "error": f"Cannot access @{username}. Profile may be private or doesn't exist."}


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


# ==================== FACEBOOK – ENHANCED WITH PROFILE DETECTION ====================
def extract_fb(url_or_at):
    url_lower = url_or_at.lower()
    
    # Personal profile detection
    if re.search(r"facebook\.com/profile\.php", url_or_at, re.I):
        return None, "profile"
    if re.search(r"facebook\.com/people/", url_or_at, re.I):
        return None, "profile"
    
    # Known personal profiles (like 'zuck')
    personal_profiles = ['zuck', 'zuckerberg', 'sherylsandberg']
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
    # Block personal profiles early
    if entity_type == "profile" or re.search(r'/(profile\.php\?|people/)', identifier, re.I):
        return {
            "success": False,
            "error": "❌ Personal Facebook profiles cannot be scraped due to privacy restrictions. Only Pages and Groups are supported. Try a Facebook Page or Group instead!"
        }

    base_url = f"https://www.facebook.com/{identifier}" if entity_type == "page" else f"https://www.facebook.com/groups/{identifier}"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })

    # ---- STRATEGY 1: mbasic (most reliable) ----
    try:
        mbasic_url = base_url.replace("www.facebook.com", "mbasic.facebook.com").replace("facebook.com", "mbasic.facebook.com")
        r = session.get(mbasic_url, timeout=20, allow_redirects=True)
        
        # Check if redirected to profile page
        if "/profile.php" in r.url or "/people/" in r.url:
            return {
                "success": False,
                "error": "❌ This is a personal Facebook profile, not a Page or Group. Personal profiles cannot be scraped!"
            }
        
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")

            name = identifier.replace("_", " ").replace("-", " ").title()
            title_tag = soup.find("title")
            if title_tag:
                name = title_tag.get_text(strip=True).split(" - ")[0].split("|")[0].strip()

            avatar = f"https://unavatar.io/facebook/{identifier}"
            
            # Multiple methods to find profile picture
            img = soup.find("img", alt=re.compile(r"profile picture|cover|photo", re.I))
            if img and img.get("src"):
                avatar = img["src"]
            else:
                # Try finding any large image
                for img_tag in soup.find_all("img")[:5]:
                    src = img_tag.get("src", "")
                    if "scontent" in src or "fbcdn" in src:
                        avatar = src
                        break

            is_group = entity_type == "group" or "/groups/" in r.url
            count = "Hidden"
            count_exact = "0"
            count_label = "members" if is_group else "followers"
            
            # Enhanced patterns for finding counts
            patterns = [
                r'([\d,]+)\s+people\s+(?:like|follow)\s+this',
                r'([\d,]+[KMB]?)\s+people\s+(?:like|follow)\s+this',
                r'([\d,]+)\s+(?:members?|likes?|followers?)',
                r'([\d,]+[KMB]?)\s+(?:members?|likes?|followers?)',
                r'>([\d,]+[KMB]?)<.*?(?:member|follower|like)',
            ]
            for pat in patterns:
                m = re.search(pat, r.text, re.I)
                if m:
                    parsed = parse_count(m.group(1).replace(",", ""))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        count = format_count(parsed, count_label)
                        break

            bio = ""
            about = soup.find("div", {"id": "about_basics"}) or soup.find("div", string=re.compile("About", re.I))
            if about:
                bio = about.get_text(strip=True)[:250]

            if count != "Hidden" or name != identifier.replace("_", " ").title():
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
        print(f"mbasic failed: {e}")

    # ---- STRATEGY 2: Desktop ----
    try:
        r = session.get(base_url, timeout=20, allow_redirects=True)
        
        # Check if redirected to profile
        if "/profile.php" in r.url or "/people/" in r.url:
            return {
                "success": False,
                "error": "❌ This is a personal Facebook profile, not a Page or Group. Personal profiles cannot be scraped!"
            }
        
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")
            name = identifier.replace("_", " ").replace("-", " ").title()
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                name = re.sub(r"\s*[-|]\s*Facebook$", "", og_title["content"], flags=re.I).strip()

            avatar = f"https://unavatar.io/facebook/{identifier}"
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                avatar = og_img["content"]

            is_group = entity_type == "group" or "/groups/" in r.url
            count = "Hidden"
            count_exact = "0"
            count_label = "members" if is_group else "followers"
            
            for pat in [r'"follower_count":(\d+)', r'"member_count":(\d+)', r'([\d,]+)\s+(?:followers?|members?)']:
                m = re.search(pat, r.text)
                if m:
                    parsed = parse_count(m.group(1).replace(",", ""))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        count = format_count(parsed, count_label)
                        break

            bio = ""
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                bio = og_desc.get("content", "")[:250]

            if count != "Hidden" or name != identifier.replace("_", " ").title():
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
        print(f"Desktop failed: {e}")

    # ---- FALLBACK: unavatar ----
    try:
        if requests.head(f"https://unavatar.io/facebook/{identifier}", timeout=5).status_code == 200:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": identifier.replace("_", " ").replace("-", " ").title(),
                "type": "Group" if entity_type == "group" else "Page",
                "count": "Hidden",
                "count_exact": "0",
                "count_label": "",
                "profile_url": base_url,
                "profile_image": f"https://unavatar.io/facebook/{identifier}",
                "bio": "Profile exists. Facebook requires login for stats.",
                "method": "unavatar",
            }
    except:
        pass

    return {"success": False, "error": f"Cannot access Facebook {entity_type}: {identifier}. It may be private or doesn't exist."}


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
            return jsonify({"success": False, "error": "❌ Personal Facebook profiles cannot be scraped. Only Pages and Groups are supported!"}), 400
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
        return jsonify({"success": False, "error": "❌ Personal Facebook profiles cannot be scraped. Only Pages and Groups!"}), 400
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
        "version": "16.0 - Instagram Profile Pics Fixed + Facebook Profile Detection",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/instagram": "POST – Instagram (multi-source profile pics)",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram (real photo)",
            "/api/facebook": "POST – Facebook Pages & Groups (blocks personal profiles)",
            "/api/health": "GET – health check",
        },
        "notes": {
            "instagram": "Uses imginn.com + picuki.com + Instagram web for reliable profile pictures",
            "facebook": "Detects and blocks personal profiles like 'zuck' – only Pages & Groups work"
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
