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


# ==================== TWITTER - ENHANCED ====================
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

    # Try Nitter instances with improved parsing
    for base in NITTER_INSTANCES:
        try:
            r = requests.get(f"{base}/{username}", headers=headers, timeout=15, allow_redirects=True)
            if r.status_code != 200: 
                continue
            
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Check if profile exists
            if "User not found" in r.text or "doesn't exist" in r.text:
                continue

            # Extract name
            name = username
            name_elem = soup.find("a", class_="profile-card-fullname")
            if not name_elem:
                name_elem = soup.find("div", class_="profile-card-fullname")
            if name_elem:
                name = name_elem.get_text(strip=True)

            # Extract bio
            bio = ""
            bio_elem = soup.find("p", class_="profile-bio")
            if bio_elem:
                bio = bio_elem.get_text(strip=True)

            # Extract stats - Try multiple methods
            tweets = following = followers = "0"
            
            # Method 1: Direct stat spans
            stats = soup.find_all("span", class_="profile-stat-num")
            if len(stats) >= 3:
                tweets, following, followers = [s.get_text(strip=True) for s in stats[:3]]
            else:
                # Method 2: Stat list items
                stat_list = soup.find("ul", class_="profile-statlist")
                if stat_list:
                    for li in stat_list.find_all("li"):
                        header = li.find("span", class_="profile-stat-header")
                        value = li.find("span", class_="profile-stat-num")
                        if header and value:
                            header_text = header.get_text(strip=True).lower()
                            value_text = value.get_text(strip=True)
                            if "tweet" in header_text or "post" in header_text:
                                tweets = value_text
                            elif "following" in header_text:
                                following = value_text
                            elif "follower" in header_text:
                                followers = value_text

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

            # Return if we got valid data
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

    # Fallback: Check if profile exists using unavatar
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
                "bio": "Profile exists but stats are currently unavailable. Try again later.",
                "method": "unavatar",
            }
    except Exception:
        pass

    return {"success": False, "error": f"@{username} not found or temporarily unavailable"}


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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }

    # Method 1: Try Instagram web scraping with multiple techniques
    try:
        r = requests.get(f"https://www.instagram.com/{username}/", headers=headers, timeout=15, allow_redirects=True)
        
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Extract from meta tags
            name = username
            bio = ""
            followers = "Hidden"
            following = "Hidden"
            posts = "Hidden"
            avatar = f"https://unavatar.io/instagram/{username}"
            
            # Try meta description
            meta_desc = soup.find("meta", {"name": "description"})
            if meta_desc:
                desc_text = meta_desc.get("content", "")
                # Format: "123K Followers, 456 Following, 789 Posts - See Instagram photos..."
                name_match = re.search(r'^([^-]+?)\s*\(', desc_text)
                if name_match:
                    name = name_match.group(1).strip()
                
                # Extract stats from description
                followers_match = re.search(r'([\d,\.]+[KMB]?)\s*Followers', desc_text, re.I)
                if followers_match:
                    followers = format_count(parse_count(followers_match.group(1)), "followers")
                
                following_match = re.search(r'([\d,\.]+[KMB]?)\s*Following', desc_text, re.I)
                if following_match:
                    following = format_count(parse_count(following_match.group(1)), "following")
                
                posts_match = re.search(r'([\d,\.]+[KMB]?)\s*Posts', desc_text, re.I)
                if posts_match:
                    posts = format_count(parse_count(posts_match.group(1)), "posts")
                
                # Extract bio (after the dash)
                bio_match = re.search(r'-\s*(.+?)(?:\s*See Instagram photos|$)', desc_text)
                if bio_match:
                    bio = bio_match.group(1).strip()
            
            # Try JSON-LD
            json_ld = soup.find("script", {"type": "application/ld+json"})
            if json_ld:
                try:
                    data = json.loads(json_ld.string)
                    if isinstance(data, dict):
                        if data.get("name"):
                            name = data["name"]
                        if data.get("description"):
                            bio = data["description"][:200]
                        if data.get("image"):
                            avatar = data["image"]
                except:
                    pass
            
            # Try og:image for avatar
            og_image = soup.find("meta", {"property": "og:image"})
            if og_image and og_image.get("content"):
                avatar = og_image["content"]
            
            # Try to find shared data in script tags
            for script in soup.find_all("script"):
                if script.string and "window._sharedData" in script.string:
                    # Extract user data from shared data
                    match = re.search(r'"full_name":"([^"]+)"', script.string)
                    if match:
                        name = match.group(1)
                    
                    bio_match = re.search(r'"biography":"([^"]+)"', script.string)
                    if bio_match:
                        bio = bio_match.group(1).replace("\\n", " ")[:200]
                    
                    followers_match = re.search(r'"edge_followed_by":\{"count":(\d+)\}', script.string)
                    if followers_match:
                        followers = format_count(followers_match.group(1), "followers")
                    
                    following_match = re.search(r'"edge_follow":\{"count":(\d+)\}', script.string)
                    if following_match:
                        following = format_count(following_match.group(1), "following")
                    
                    posts_match = re.search(r'"edge_owner_to_timeline_media":\{"count":(\d+)\}', script.string)
                    if posts_match:
                        posts = format_count(posts_match.group(1), "posts")
                    
                    avatar_match = re.search(r'"profile_pic_url_hd":"([^"]+)"', script.string)
                    if avatar_match:
                        avatar = avatar_match.group(1).replace("\\/", "/")
                    break
            
            # If we got at least the name, return success
            if name != username or bio or followers != "Hidden":
                return {
                    "success": True,
                    "platform": "instagram",
                    "username": username,
                    "name": name,
                    "follower_count": followers,
                    "following_count": following,
                    "post_count": posts,
                    "profile_url": f"https://instagram.com/{username}",
                    "profile_image": avatar,
                    "bio": bio if bio else "Profile exists but bio requires login to view",
                    "method": "instagram_scrape",
                }
    
    except Exception as e:
        print(f"Instagram scrape error for {username}: {e}")

    # Method 2: Fallback to unavatar check
    try:
        avatar_check = requests.head(f"https://unavatar.io/instagram/{username}", timeout=5)
        if avatar_check.status_code == 200:
            return {
                "success": True,
                "platform": "instagram",
                "username": username,
                "name": username.title(),
                "follower_count": "Hidden",
                "following_count": "Hidden",
                "post_count": "Hidden",
                "profile_url": f"https://instagram.com/{username}",
                "profile_image": f"https://unavatar.io/instagram/{username}",
                "bio": "Profile exists but full stats require login to view",
                "method": "unavatar",
            }
    except Exception:
        pass

    return {"success": False, "error": f"@{username} not found or is private"}


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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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


# ==================== FACEBOOK - FIXED ====================
def extract_fb(url_or_at):
    """Extract identifier from Facebook URL - IMPROVED"""
    url_lower = url_or_at.lower()
    
    # Priority 1: Check for numeric group ID
    m = re.search(r"facebook\.com/groups/(\d+)", url_or_at, re.I)
    if m:
        return m.group(1), "group"
    
    # Priority 2: Check for groups with username
    m = re.search(r"facebook\.com/groups/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m:
        return m.group(1), "group"
    
    # Priority 3: Regular page/profile URLs
    m = re.search(r"(?:facebook\.com|fb\.com)/([A-Za-z0-9.\-_]+)", url_or_at, re.I)
    if m:
        ident = m.group(1)
        # Skip common non-profile paths
        if ident.lower() not in {"profile.php", "watch", "marketplace", "groups", "sharer", "share", "pages", "people"}:
            return ident, "page"
    
    # Priority 4: Plain username (assume page)
    if "facebook.com" not in url_lower and "fb.com" not in url_lower:
        m = re.search(r"^@?([A-Za-z0-9.\-_]+)$", url_or_at)
        if m:
            return m.group(1), "page"
    
    return None, None


def scrape_facebook(identifier, entity_type="page"):
    """Enhanced Facebook scraper with aggressive data extraction"""
    
    # Build URL
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
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0"
    }

    try:
        # Make request with session
        session = requests.Session()
        r = session.get(url, headers=headers, timeout=20, allow_redirects=True)
        
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")

        soup = BeautifulSoup(r.text, "html.parser")
        text_content = r.text

        # Check if redirected to login (completely private)
        if "login" in r.url.lower() or "checkpoint" in r.url.lower():
            raise ValueError("Login required - completely private")

        # Extract title and name
        title_tag = soup.find("title")
        raw_title = title_tag.get_text(strip=True) if title_tag else ""
        
        # Clean title aggressively
        name = raw_title
        name = re.sub(r"\s*[-|·]\s*Facebook\s*$", "", name, flags=re.I)
        name = re.sub(r"^(Log into Facebook|Log In or Sign Up to View|Facebook|Meta)\s*[-|·]?\s*", "", name, flags=re.I)
        name = name.strip()
        
        # If name is still generic or empty, use identifier
        if not name or len(name) < 2 or name.lower() in ["facebook", "log into facebook", "meta"]:
            name = identifier.replace("_", " ").replace("-", " ").replace(".", " ").title()
        
        # Determine actual type
        is_group = "/groups/" in r.url or entity_type == "group" or "group" in raw_title.lower()
        
        count = "Hidden"
        count_exact = "0"
        count_label = ""

        # Extract stats with multiple patterns
        if is_group:
            # Group member patterns - more aggressive
            patterns = [
                r'(\d{1,3}(?:,\d{3})*)\s*members?',
                r'(\d+\.?\d*[KMB])\s*members?',
                r'members?\s*[:\-·]?\s*(\d{1,3}(?:,\d{3})*)',
                r'"member_count":\s*(\d+)',
                r'"group_member_count":\s*(\d+)',
                r'data-count="(\d+)".*?members?',
                r'members?.*?(\d{1,3}(?:,\d{3})*)',
            ]
            
            for pattern in patterns:
                m = re.search(pattern, text_content, re.I | re.DOTALL)
                if m:
                    count_str = m.group(1).replace(",", "").strip()
                    parsed = parse_count(count_str)
                    if parsed > 0:
                        count_exact = str(parsed)
                        count = format_count(count_exact, "members")
                        count_label = "members"
                        break
            
            entity_type_label = "Group"
        else:
            # Page followers/likes patterns - more aggressive
            patterns = [
                r'(\d{1,3}(?:,\d{3})*)\s*(?:people\s+)?(?:like|follow)',
                r'(\d+\.?\d*[KMB])\s*(?:followers?|likes?)',
                r'(?:followers?|likes?)\s*[:\-·]?\s*(\d{1,3}(?:,\d{3})*)',
                r'"follower_count":\s*(\d+)',
                r'"page_likers":\s*(\d+)',
                r'data-count="(\d+)".*?(?:like|follow)',
                r'(?:like|follow).*?(\d{1,3}(?:,\d{3})*)',
            ]
            
            for pattern in patterns:
                m = re.search(pattern, text_content, re.I | re.DOTALL)
                if m:
                    count_str = m.group(1).replace(",", "").strip()
                    parsed = parse_count(count_str)
                    if parsed > 0:
                        count_exact = str(parsed)
                        # Determine if it's followers or likes
                        if "follow" in pattern.lower() or "follower" in text_content[max(0, m.start()-50):m.end()+50].lower():
                            count = format_count(count_exact, "followers")
                            count_label = "followers"
                        else:
                            count = format_count(count_exact, "likes")
                            count_label = "likes"
                        break
            
            entity_type_label = "Page"

        # Extract description/bio aggressively
        bio = ""
        
        # Try meta description
        meta_desc = soup.find("meta", {"name": "description"})
        if not meta_desc:
            meta_desc = soup.find("meta", {"property": "og:description"})
        if meta_desc and meta_desc.get("content"):
            bio_text = meta_desc.get("content", "").strip()
            # Clean up common Facebook meta patterns
            bio_text = re.sub(r'^.*?[·|]\s*', '', bio_text)  # Remove prefix before separator
            bio_text = re.sub(r'\s*Log In.*


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
        
        # Try Instagram
        res = scrape_instagram(user)
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
        "supported": ["YouTube", "Twitter/X", "Instagram", "TikTok", "Telegram", "Facebook (Pages & Groups)"],
    })

@app.route("/api/info", methods=["GET"])
def info():
    return jsonify({
        "message": "Social Media Stats API",
        "version": "11.0",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/instagram": "POST – Instagram",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram",
            "/api/facebook": "POST – Facebook (Pages & Groups)",
            "/api/health": "GET – health check",
        },
    })


# --------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True), '', bio_text, flags=re.I)  # Remove login suffix
            bio_text = re.sub(r'\s*See more of.*


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
        
        # Try Instagram
        res = scrape_instagram(user)
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
        "supported": ["YouTube", "Twitter/X", "Instagram", "TikTok", "Telegram", "Facebook (Pages & Groups)"],
    })

@app.route("/api/info", methods=["GET"])
def info():
    return jsonify({
        "message": "Social Media Stats API",
        "version": "11.0",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/instagram": "POST – Instagram",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram",
            "/api/facebook": "POST – Facebook (Pages & Groups)",
            "/api/health": "GET – health check",
        },
    })


# --------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True), '', bio_text, flags=re.I)
            bio_text = re.sub(r'\s*\d+\s*(?:people|likes|followers).*', '', bio_text, flags=re.I)
            if len(bio_text) > 10:
                bio = bio_text[:250]
        
        # Try to find about/description in page content
        if not bio:
            about_patterns = [
                r'<div[^>]*about[^>]*>([^<]{10,200})</div>',
                r'<meta[^>]*description[^>]*content="([^"]{10,200})"',
                r'"description":"([^"]{10,200})"',
            ]
            for pattern in about_patterns:
                m = re.search(pattern, text_content, re.I)
                if m:
                    bio = m.group(1).strip()[:250]
                    break

        # Extract profile image
        img_url = f"https://unavatar.io/facebook/{identifier}"
        
        # Try og:image
        og_image = soup.find("meta", {"property": "og:image"})
        if og_image and og_image.get("content"):
            img_url = og_image["content"]
        
        # Try to find profile image in content
        if not og_image:
            img_match = re.search(r'"profile_picture":\{"uri":"([^"]+)"', text_content)
            if img_match:
                img_url = img_match.group(1).replace("\\/", "/")

        # Build response
        result = {
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
            "bio": bio if bio else f"Facebook {entity_type_label.lower()} exists but full details may require login",
            "method": "facebook_scrape",
        }
        
        # Add note if stats are hidden
        if count == "Hidden":
            result["note"] = f"This {entity_type_label.lower()}'s stats are private or require login to view"
        
        return result

    except Exception as e:
        print(f"Facebook scrape error for {identifier}: {e}")

    # Ultimate fallback - try mbasic.facebook.com (mobile site)
    try:
        mobile_url = url.replace("www.facebook.com", "mbasic.facebook.com")
        r = requests.get(mobile_url, headers=headers, timeout=15)
        
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Extract name from mobile view
            name = identifier.replace("_", " ").replace("-", " ").title()
            h3_tag = soup.find("h3")
            if h3_tag:
                name = h3_tag.get_text(strip=True)
            
            # Try to find member/follower count in mobile view
            count = "Hidden"
            count_label = ""
            
            count_match = re.search(r'(\d{1,3}(?:,\d{3})*)\s*(?:members?|followers?|likes?)', r.text, re.I)
            if count_match:
                parsed = parse_count(count_match.group(1))
                if parsed > 0:
                    if is_group:
                        count = format_count(str(parsed), "members")
                        count_label = "members"
                    else:
                        count = format_count(str(parsed), "followers")
                        count_label = "followers"
            
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": name,
                "type": "Group" if is_group else "Page",
                "count": count,
                "count_exact": str(parse_count(count)) if count != "Hidden" else "0",
                "count_label": count_label,
                "profile_url": url,
                "profile_image": f"https://unavatar.io/facebook/{identifier}",
                "bio": "Limited data available from mobile view",
                "method": "facebook_mobile",
                "note": "Stats extracted from mobile version" if count != "Hidden" else "Profile exists but stats require login"
            }
    except Exception as e:
        print(f"Facebook mobile scrape failed: {e}")

    # Final fallback - unavatar check
    try:
        avatar_url = f"https://unavatar.io/facebook/{identifier}"
        check = requests.head(avatar_url, timeout=5)
        if check.status_code == 200:
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": identifier.replace("_", " ").replace("-", " ").title(),
                "type": "Page" if entity_type == "page" else "Group",
                "count": "Hidden",
                "count_exact": "0",
                "count_label": "",
                "profile_url": url,
                "profile_image": avatar_url,
                "bio": "Profile confirmed to exist but requires Facebook login for full details",
                "method": "unavatar",
                "note": "Login required to view full profile data"
            }
    except Exception:
        pass

    return {"success": False, "error": f"Cannot access Facebook profile: {identifier}"}


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
        
        # Try Instagram
        res = scrape_instagram(user)
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
        "supported": ["YouTube", "Twitter/X", "Instagram", "TikTok", "Telegram", "Facebook (Pages & Groups)"],
    })

@app.route("/api/info", methods=["GET"])
def info():
    return jsonify({
        "message": "Social Media Stats API",
        "version": "11.0",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/instagram": "POST – Instagram",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram",
            "/api/facebook": "POST – Facebook (Pages & Groups)",
            "/api/health": "GET – health check",
        },
    })


# --------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
