# --------------------------------------------------------------
# app.py – Social-Media Stats API v20 (WITH VERIFICATION CODE SUPPORT)
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


def check_verification_code(text, code):
    """Check if verification code exists in text"""
    if not text or not code:
        return False
    return code.upper() in text.upper()


# ==================== YOUTUBE ====================
def extract_yt(url):
    m = re.search(r"youtube\.com/channel/(UC[\w-]+)", url)
    if m: return m.group(1), "id"
    m = re.search(r"youtube\.com/@([\w-]+)", url)
    if m: return m.group(1), "handle"
    return None, None


def yt_by_id(cid, verification_code=None):
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
    
    description = snippet.get("description", "")
    verified = False
    if verification_code:
        verified = check_verification_code(description, verification_code)
    
    return {
        "success": True,
        "platform": "youtube",
        "username": snippet["title"],
        "name": snippet["title"],
        "subscriber_count": format_count(stats.get("subscriberCount", 0), "subscribers"),
        "subscriber_count_exact": stats.get("subscriberCount", "0"),
        "video_count": stats.get("videoCount", "0"),
        "view_count": format_count(stats.get("viewCount", 0), "views"),
        "profile_url": f"https://www.youtube.com/channel/{cid}",
        "profile_image": snippet["thumbnails"]["high"]["url"],
        "description": description[:300],
        "bio": description[:300],
        "verificationCodeFound": verified,
        "isVerified": snippet.get("customUrl") is not None,  # YouTube verified badge
    }


def yt_by_handle(handle, verification_code=None):
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
    return yt_by_id(cid, verification_code)


# ==================== TWITTER (SUPER FIXED) ====================
NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://nitter.cz",
    "https://nitter.1d4.us",
    "https://nitter.unixfox.eu",
]

def extract_tw(url_or_at):
    m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_twitter(username, verification_code=None):
    username = username.lstrip("@")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    for base in NITTER_INSTANCES:
        try:
            print(f"[Twitter] Trying {base} for @{username}...")
            r = requests.get(f"{base}/{username}", headers=headers, timeout=15, allow_redirects=True)
            
            if r.status_code != 200:
                continue
                
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Check for errors
            text_lower = r.text.lower()
            if any(err in text_lower for err in ["user not found", "doesn't exist", "suspended"]):
                continue

            # Extract name
            name = username
            name_tag = soup.find("a", class_="profile-card-fullname")
            if not name_tag:
                name_tag = soup.find("div", class_="profile-card-fullname")
            if name_tag:
                name = name_tag.get_text(strip=True)
            
            if not name or name == username:
                title = soup.find("title")
                if title:
                    title_text = title.get_text(strip=True)
                    name_match = re.match(r"^(.+?)\s*(?:/|\\u0028|@)", title_text)
                    if name_match:
                        name = name_match.group(1).strip()

            # Extract bio
            bio = ""
            bio_tag = soup.find("p", class_="profile-bio")
            if not bio_tag:
                bio_tag = soup.find("div", class_="profile-bio")
            if bio_tag:
                bio = bio_tag.get_text(strip=True)

            # Check verification code in bio
            verified = False
            if verification_code and bio:
                verified = check_verification_code(bio, verification_code)

            # Extract stats
            tweets = following = followers = "0"
            stat_nums = soup.find_all("span", class_="profile-stat-num")
            if len(stat_nums) >= 3:
                tweets = stat_nums[0].get_text(strip=True)
                following = stat_nums[1].get_text(strip=True)
                followers = stat_nums[2].get_text(strip=True)
            
            if followers == "0":
                stat_list = soup.find("ul", class_="profile-statlist")
                if stat_list:
                    for li in stat_list.find_all("li"):
                        header = li.find("span", class_="profile-stat-header")
                        value = li.find("span", class_="profile-stat-num")
                        if header and value:
                            header_text = header.get_text(strip=True).lower()
                            value_text = value.get_text(strip=True)
                            if "post" in header_text or "tweet" in header_text:
                                tweets = value_text
                            elif "following" in header_text:
                                following = value_text
                            elif "follower" in header_text:
                                followers = value_text

            follower_cnt = parse_count(followers)

            # Extract avatar
            avatar = f"https://unavatar.io/twitter/{username}"
            avatar_link = soup.find("a", class_="profile-card-avatar")
            if avatar_link:
                img = avatar_link.find("img")
                if img and img.get("src"):
                    src = img.get("src")
                    if src.startswith("http"):
                        avatar = src
                    elif src.startswith("/"):
                        avatar = base + src

            # Check if account is verified (blue check)
            is_verified = bool(soup.find("span", class_="icon-verified"))

            if follower_cnt >= 10 or (name != username and len(name) > 0) or len(bio) > 0:
                print(f"  ✓ Found: {name}, {format_count(follower_cnt, 'followers')}")
                return {
                    "success": True,
                    "platform": "twitter",
                    "username": username,
                    "name": name,
                    "bio": bio,
                    "follower_count": format_count(follower_cnt, "followers"),
                    "follower_count_exact": str(follower_cnt),
                    "following_count": format_count(parse_count(following), "following"),
                    "tweet_count": tweets,
                    "profile_url": f"https://twitter.com/{username}",
                    "profile_image": avatar,
                    "method": base.replace("https://", ""),
                    "verificationCodeFound": verified,
                    "isVerified": is_verified,
                }
                
        except Exception as e:
            print(f"  Error: {e}")
            continue

    # Fallback
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
                "bio": "Profile exists but stats are unavailable.",
                "method": "unavatar",
                "verificationCodeFound": False,
            }
    except:
        pass

    return {"success": False, "error": f"@{username} not found"}


# ==================== INSTAGRAM ====================
def extract_ig(url_or_at):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_instagram(username, verification_code=None):
    username = username.lstrip("@")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.instagram.com/",
        "x-ig-app-id": "936619743392459",
    }

    # METHOD 1: Official API
    try:
        api_url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
        r = requests.get(api_url, headers=headers, timeout=20)
        if r.status_code == 200:
            data = r.json()
            user = data.get("data", {}).get("user", {})
            if user:
                bio = user.get("biography", "")
                verified = False
                if verification_code and bio:
                    verified = check_verification_code(bio, verification_code)
                
                return {
                    "success": True,
                    "platform": "instagram",
                    "username": username,
                    "name": user.get("full_name") or username,
                    "follower_count": format_count(user.get("edge_followed_by", {}).get("count", 0), "followers"),
                    "follower_count_exact": str(user.get("edge_followed_by", {}).get("count", 0)),
                    "following_count": format_count(user.get("edge_follow", {}).get("count", 0), "following"),
                    "post_count": format_count(user.get("edge_owner_to_timeline_media", {}).get("count", 0), "posts"),
                    "profile_url": f"https://instagram.com/{username}",
                    "profile_image": user.get("profile_pic_url_hd") or user.get("profile_pic_url") or f"https://unavatar.io/instagram/{username}",
                    "bio": bio[:250],
                    "method": "instagram_api",
                    "verificationCodeFound": verified,
                    "isVerified": user.get("is_verified", False),
                }
    except Exception as e:
        print(f"Instagram API failed: {e}")

    # Fallback
    try:
        if requests.head(f"https://unavatar.io/instagram/{username}", timeout=5).status_code == 200:
            return {
                "success": True,
                "platform": "instagram",
                "username": username,
                "name": username.replace("_", " ").title(),
                "follower_count": "Hidden",
                "profile_url": f"https://instagram.com/{username}",
                "profile_image": f"https://unavatar.io/instagram/{username}",
                "bio": "Profile exists. Login required for stats.",
                "method": "unavatar",
                "verificationCodeFound": False,
            }
    except:
        pass

    return {"success": False, "error": f"Cannot access @{username}"}


# ==================== TIKTOK ====================
def extract_tt(url_or_at):
    m = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_tiktok(username, verification_code=None):
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
        
        bio = u.get("signature", "")
        verified = False
        if verification_code and bio:
            verified = check_verification_code(bio, verification_code)
        
        return {
            "success": True,
            "platform": "tiktok",
            "username": u.get("uniqueId", username),
            "name": u.get("nickname", username),
            "follower_count": format_count(s.get("followerCount", 0), "followers"),
            "follower_count_exact": str(s.get("followerCount", 0)),
            "following_count": format_count(s.get("followingCount", 0), "following"),
            "video_count": format_count(s.get("videoCount", 0), "videos"),
            "likes_count": str(s.get("heartCount", 0)),
            "profile_url": f"https://www.tiktok.com/@{username}",
            "profile_image": u.get("avatarLarger", f"https://unavatar.io/tiktok/{username}"),
            "bio": bio,
            "method": "tiktok_web",
            "verificationCodeFound": verified,
            "isVerified": u.get("verified", False),
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
                    "verificationCodeFound": False,
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
    # Handle private channel IDs
    m = re.search(r"^(-100\d+)$", url_or_at)
    if m: return m.group(1)
    return None


async def _tg_async(username, verification_code=None):
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
            
            description = full.full_chat.about or ""
            verified = False
            if verification_code and description:
                verified = check_verification_code(description, verification_code)
            
            return {
                "success": True,
                "platform": "telegram",
                "username": entity.username or username,
                "name": entity.title,
                "type": typ,
                "member_count": format_count(full.full_chat.participants_count, "members"),
                "member_count_exact": str(full.full_chat.participants_count),
                "description": description,
                "bio": description,
                "profile_url": f"https://t.me/{username}",
                "profile_image": avatar,
                "method": "telethon",
                "verificationCodeFound": verified,
            }

        full = await client(GetFullUserRequest(entity))
        name = f"{entity.first_name or ''} {entity.last_name or ''}".strip() or username
        
        bio = full.full_user.about or ""
        verified = False
        if verification_code and bio:
            verified = check_verification_code(bio, verification_code)
        
        return {
            "success": True,
            "platform": "telegram",
            "username": entity.username or username,
            "name": name,
            "type": "User",
            "bio": bio,
            "profile_url": f"https://t.me/{username}",
            "profile_image": avatar,
            "method": "telethon",
            "verificationCodeFound": verified,
        }
    except UsernameNotOccupiedError:
        return {"success": False, "error": f"@{username} not found"}
    except ChannelPrivateError:
        return {"success": False, "error": f"@{username} is private"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        await client.disconnect()


def scrape_telegram(username, verification_code=None):
    return asyncio.run(_tg_async(username.lstrip("@"), verification_code))


# ==================== FACEBOOK ====================
def extract_fb(url_or_at):
    if re.search(r"facebook\.com/profile\.php", url_or_at, re.I):
        return None, "profile"
    if re.search(r"facebook\.com/people/", url_or_at, re.I):
        return None, "profile"
    
    m = re.search(r"facebook\.com/groups/(\d+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    m = re.search(r"facebook\.com/groups/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m: return m.group(1), "group"
    
    m = re.search(r"(?:facebook\.com|fb\.com)/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m:
        ident = m.group(1)
        if ident.lower() not in {"profile.php", "watch", "marketplace", "groups", "sharer", "pages", "people"}:
            return ident, "page"
    
    m = re.search(r"^@?([A-Za-z0-9.\-_]+)$", url_or_at)
    if m: return m.group(1), "page"
    
    return None, None


def scrape_facebook(identifier, entity_type="page", verification_code=None):
    if entity_type == "profile":
        return {"success": False, "error": "❌ Personal profiles not supported"}

    base_url = f"https://www.facebook.com/{identifier}" if entity_type == "page" else f"https://www.facebook.com/groups/{identifier}"
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # STRATEGY 1: mbasic (most reliable)
    try:
        print(f"[Facebook] Trying mbasic for {identifier}...")
        mbasic_url = f"https://mbasic.facebook.com/{identifier}" if entity_type == "page" else f"https://mbasic.facebook.com/groups/{identifier}"
        r = session.get(mbasic_url, timeout=20, allow_redirects=True)
        
        if "/profile.php" in r.url or "/people/" in r.url:
            return {"success": False, "error": "❌ Personal profile"}
        
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")

            # Extract name
            name = identifier.replace("_", " ").replace("-", " ").title()
            title = soup.find("title")
            if title:
                title_text = title.get_text(strip=True)
                name = re.sub(r"\s*[-|]\s*Facebook\s*$", "", title_text, flags=re.I).strip()
                name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
            
            if not name or name == identifier:
                for tag in soup.find_all(["h1", "h3"], limit=5):
                    text = tag.get_text(strip=True)
                    if text and len(text) > 2 and text.lower() != "facebook":
                        name = text
                        break

            # Avatar
            avatar = f"https://unavatar.io/facebook/{identifier}"
            for img in soup.find_all("img", limit=10):
                src = img.get("src", "")
                if "scontent" in src or ("fbcdn" in src and "https://" in src):
                    avatar = src
                    break

            # Bio/Description
            bio = ""
            meta_desc = soup.find("meta", {"name": "description"})
            if meta_desc:
                bio = meta_desc.get("content", "")[:300]
            
            # Check verification code in bio
            verified = False
            if verification_code and bio:
                verified = check_verification_code(bio, verification_code)

            # Stats
            is_group = entity_type == "group" or "/groups/" in r.url
            count_label = "members" if is_group else "followers"
            count = "Hidden"
            count_exact = "0"
            
            visible_text = soup.get_text()
            patterns = [
                r'([\d,]+)\s+people\s+(?:like|follow)',
                r'([\d,]+)\s+(?:likes|followers?|members?)',
                r'([\d,.]+[KMB]?)\s+(?:people|likes|followers?|members?)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, visible_text, re.I)
                if match:
                    parsed = parse_count(match.group(1).replace(",", ""))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        count = format_count(parsed, count_label)
                        break

            print(f"  ✓ Found: {name}, {count}")
            
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": name,
                "type": "Group" if is_group else "Page",
                "count": count,
                "count_exact": count_exact,
                "follower_count_exact": count_exact,
                "member_count_exact": count_exact,
                "count_label": count_label,
                "profile_url": base_url,
                "profile_image": avatar,
                "bio": bio or "Facebook Page/Group",
                "method": "facebook_mbasic",
                "verificationCodeFound": verified,
            }
    except Exception as e:
        print(f"  mbasic error: {e}")



    # STRATEGY 2: Desktop
    try:
        print(f"[Facebook] Trying desktop for {identifier}...")
        r = session.get(base_url, timeout=20, allow_redirects=True)
        
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Name from og:title (most reliable for desktop)
            name = identifier.replace("_", " ").replace("-", " ").title()
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                name = re.sub(r"\s*[-|]\s*Facebook\s*$", "", og_title["content"], flags=re.I).strip()

            # Avatar
            avatar = f"https://unavatar.io/facebook/{identifier}"
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                avatar = og_img["content"]

            # Stats
            is_group = entity_type == "group" or "/groups/" in r.url
            count_label = "members" if is_group else "followers"
            count = "Hidden"
            count_exact = "0"
            
            # Try JSON data
            json_patterns = [
                r'"follower_count":(\d+)',
                r'"member_count":(\d+)',
                r'"fan_count":(\d+)',
            ]
            for pattern in json_patterns:
                match = re.search(pattern, r.text)
                if match:
                    parsed = int(match.group(1))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        count = format_count(parsed, count_label)
                        break

            # Bio
            bio = ""
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                bio = og_desc.get("content", "")[:300]
            
            print(f"  ✓ Found: {name}, {count}")

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
        print(f"  desktop error: {e}")

    # Fallback
    try:
        if requests.head(f"https://unavatar.io/facebook/{identifier}", timeout=5).status_code == 200:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": identifier.replace("_", " ").replace("-", " ").title(),
                "type": "Group" if entity_type == "group" else "Page",
                "count": "Hidden",
                "profile_url": base_url,
                "profile_image": f"https://unavatar.io/facebook/{identifier}",
                "bio": "Profile exists. Login required.",
                "method": "unavatar",
            }
    except:
        pass

    return {"success": False, "error": f"Cannot access {identifier}"}


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
