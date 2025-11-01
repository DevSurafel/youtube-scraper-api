# --------------------------------------------------------------
# app.py  –  Social-Media Stats API (Instagram & Facebook FIXED)
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


# ==================== INSTAGRAM – COMPLETE FIX WITH HD AVATAR ====================
def extract_ig(url_or_at):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_instagram(username):
    """
    Complete Instagram scraper with HD profile picture extraction
    """
    username = username.lstrip("@")
    
    # Method 1: Official Instagram API
    api_url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
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
    
    try:
        print(f"Trying Instagram API for {username}...")
        r = requests.get(api_url, headers=headers, timeout=20)
        
        if r.status_code == 200:
            data = r.json()
            user = data.get("data", {}).get("user", {})
            
            if user:
                followers = user.get("edge_followed_by", {}).get("count", 0)
                following = user.get("edge_follow", {}).get("count", 0)
                posts = user.get("edge_owner_to_timeline_media", {}).get("count", 0)
                full_name = user.get("full_name") or username
                bio = user.get("biography") or ""
                
                # CRITICAL: Get HD profile picture
                avatar = (
                    user.get("profile_pic_url_hd") or 
                    user.get("profile_pic_url") or 
                    f"https://unavatar.io/instagram/{username}"
                )
                
                is_verified = user.get("is_verified", False)
                is_private = user.get("is_private", False)
                
                print(f"✓ Instagram API success for {username}")
                print(f"  Avatar URL: {avatar[:100]}...")
                
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
                    "method": "instagram_api",
                }
    except Exception as e:
        print(f"Instagram API failed: {e}")
    
    # Method 2: Web scraping with better avatar extraction
    try:
        print(f"Trying web scraping for {username}...")
        web_url = f"https://www.instagram.com/{username}/"
        r = requests.get(web_url, headers=headers, timeout=15)
        
        if r.status_code == 200:
            # Try to extract from meta tags first
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Get avatar from og:image (usually HD)
            og_img = soup.find("meta", property="og:image")
            avatar = f"https://unavatar.io/instagram/{username}"
            
            if og_img and og_img.get("content"):
                img_url = og_img["content"]
                # Instagram og:image is usually high quality
                if img_url.startswith("http"):
                    avatar = img_url
                    print(f"✓ Found avatar from og:image: {avatar[:80]}...")
            
            # Try JSON-LD data
            json_ld = soup.find("script", {"type": "application/ld+json"})
            if json_ld:
                try:
                    ld_data = json.loads(json_ld.string)
                    name = ld_data.get("name", username)
                    
                    # Sometimes profile image is in JSON-LD
                    if "image" in ld_data and ld_data["image"].startswith("http"):
                        avatar = ld_data["image"]
                        print(f"✓ Found avatar from JSON-LD: {avatar[:80]}...")
                except:
                    pass
            
            # Extract stats from meta description
            meta = soup.find("meta", property="og:description")
            if meta:
                txt = meta.get("content", "")
                name_match = re.search(r"^([^-(]+)", txt)
                name = name_match.group(1).strip() if name_match else username
                
                followers_match = re.search(r"([\d,.]+[KMB]?)\s*Followers?", txt, re.I)
                following_match = re.search(r"([\d,.]+[KMB]?)\s*Following", txt, re.I)
                posts_match = re.search(r"([\d,.]+[KMB]?)\s*Posts?", txt, re.I)
                
                bio_match = re.search(r"-\s*(.+?)(?:\s*See Instagram|$)", txt)
                bio = bio_match.group(1).strip()[:250] if bio_match else ""
                
                if followers_match or posts_match:
                    print(f"✓ Web scraping success for {username}")
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
                        "profile_image": avatar,
                        "bio": bio,
                        "method": "instagram_web",
                    }
    except Exception as e:
        print(f"Web scraping failed: {e}")
    
    # Method 3: Try alternative Instagram viewer services
    try:
        print(f"Trying Imginn for {username}...")
        imginn_url = f"https://imginn.com/{username}/"
        r = requests.get(imginn_url, headers={"User-Agent": headers["User-Agent"]}, timeout=15)
        
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Get avatar
            avatar_img = soup.find("img", class_=lambda x: x and "avatar" in x.lower()) or soup.find("img", alt=lambda x: x and username in str(x).lower())
            avatar = f"https://unavatar.io/instagram/{username}"
            
            if avatar_img and avatar_img.get("src"):
                src = avatar_img["src"]
                if src.startswith("http"):
                    avatar = src
                    print(f"✓ Found avatar from Imginn: {avatar[:80]}...")
            
            # Get stats
            name = username.title()
            name_elem = soup.find("h1") or soup.find("h2")
            if name_elem:
                name = name_elem.get_text(strip=True)
            
            stats_text = soup.get_text()
            followers_match = re.search(r"([\d,.]+[KMB]?)\s*(?:Followers?|followers?)", stats_text)
            following_match = re.search(r"([\d,.]+[KMB]?)\s*(?:Following|following)", stats_text)
            posts_match = re.search(r"([\d,.]+[KMB]?)\s*(?:Posts?|posts?)", stats_text)
            
            if followers_match or avatar.startswith("http"):
                print(f"✓ Imginn success for {username}")
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
                    "profile_image": avatar,
                    "bio": "",
                    "method": "imginn",
                }
    except Exception as e:
        print(f"Imginn failed: {e}")
    
    # Final fallback: unavatar
    try:
        unavatar_url = f"https://unavatar.io/instagram/{username}"
        if requests.head(unavatar_url, timeout=5).status_code == 200:
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
    
    return {
        "success": False,
        "error": f"Cannot access @{username}. Profile may be private or Instagram is blocking requests."
    }


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

        # Download real photo
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


# ==================== FACEBOOK – COMPLETE REWRITE ====================
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
    """
    Complete Facebook scraper using mbasic (most reliable)
    """
    base_url = f"https://www.facebook.com/{identifier}" if entity_type == "page" else f"https://www.facebook.com/groups/{identifier}"
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })
    
    # Strategy 1: mbasic (MOST RELIABLE)
    try:
        print(f"Trying mbasic.facebook.com for {identifier}...")
        mbasic_url = base_url.replace("www.facebook.com", "mbasic.facebook.com").replace("facebook.com", "mbasic.facebook.com")
        
        r = session.get(mbasic_url, timeout=20, allow_redirects=True)
        
        if r.status_code == 200 and "login" not in r.url.lower():
            html = r.text
            soup = BeautifulSoup(html, "html.parser")
            
            # Extract name
            name = identifier.replace("_", " ").replace("-", " ").title()
            
            # Try multiple name sources
            title_tag = soup.find("title")
            if title_tag:
                title_text = title_tag.get_text(strip=True)
                if title_text and len(title_text) < 100 and "facebook" not in title_text.lower():
                    name = title_text
            
            # Get from page header
            header_div = soup.find("div", {"id": "m-timeline-cover-section"})
            if header_div:
                h_tag = header_div.find(["h1", "h2", "h3"])
                if h_tag:
                    name = h_tag.get_text(strip=True)
            
            # Extract profile picture
            avatar = f"https://unavatar.io/facebook/{identifier}"
            
            # Look for profile image in various places
            profile_pic = soup.find("img", alt=lambda x: x and (name.lower() in str(x).lower() or "profile picture" in str(x).lower()))
            if profile_pic and profile_pic.get("src"):
                src = profile_pic["src"]
                if src.startswith("http"):
                    avatar = src
                    print(f"✓ Found avatar from mbasic img: {avatar[:80]}...")
            
            # Extract follower/member count
            is_group = entity_type == "group" or "/groups/" in r.url
            count = "Hidden"
            count_exact = "0"
            count_label = ""
            
            # Comprehensive patterns for mbasic
            patterns = [
                (r'([\d,]+)\s+people\s+(?:like|follow)\s+this', 'followers'),
                (r'([\d,]+[KMB]?)\s+people\s+(?:like|follow)\s+this', 'followers'),
                (r'([\d,]+)\s+(?:members?|likes?|followers?)', 'members' if is_group else 'followers'),
                (r'([\d,]+[KMB]?)\s+(?:members?|likes?|followers?)', 'members' if is_group else 'followers'),
                (r'>([\d,]+)<.*?(?:member|like|follow)', 'members' if is_group else 'followers'),
            ]
            
            for pattern, label_type in patterns:
                matches = re.findall(pattern, html, re.I | re.DOTALL)
                if matches:
                    for match in matches:
                        parsed = parse_count(str(match).replace(",", ""))
                        if parsed >= 10:  # Filter noise
                            count_exact = str(parsed)
                            count = format_count(parsed, label_type)
                            count_label = label_type
                            print(f"✓ Found count: {count} ({label_type})")
                            break
                if count != "Hidden":
                    break
            
            # Extract bio/description
            bio = ""
            about_div = soup.find("div", {"id": "about_basics"})
            if about_div:
                bio = about_div.get_text(strip=True)[:250]
            
            if count != "Hidden" or name != identifier.replace("_", " ").title():
                print(f"✓ mbasic success for {identifier}")
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
    
    # Strategy 2: Desktop with better extraction
    try:
        print(f"Trying desktop facebook.com for {identifier}...")
        r = session.get(base_url, timeout=20, allow_redirects=True)
        
        if r.status_code == 200 and "login" not in r.url.lower():
            html = r.text
            soup = BeautifulSoup(html, "html.parser")
            
            # Name extraction
            name = identifier.replace("_", " ").replace("-", " ").title()
            
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title_text = og_title["content"]
                title_text = re.sub(r"\s*[-|]\s*Facebook$", "", title_text, flags=re.I)
                if title_text and "facebook" not in title_text.lower():
                    name = title_text
            
            # Avatar extraction
            avatar = f"https://unavatar.io/facebook/{identifier}"
            
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                img_url = og_img["content"]
                if img_url.startswith("http"):
                    avatar = img_url
                    print(f"✓ Found avatar from og:image: {avatar[:80]}...")
            
            # Try to find profile image in the page
            if avatar == f"https://unavatar.io/facebook/{identifier}":
                img_tags = soup.find_all("img", src=lambda x: x and "scontent" in str(x))
                for img in img_tags:
                    src = img.get("src")
                    if src and src.startswith("http") and "scontent" in src:
                        avatar = src
                        print(f"✓ Found avatar from scontent img: {avatar[:80]}...")
                        break
            
            # Stats extraction
            is_group = entity_type == "group" or "/groups/" in r.url
            count = "Hidden"
            count_exact = "0"
            count_label = ""
            
            # Desktop patterns
            patterns = [
                r'"follower_count":(\d+)',
                r'"member_count":(\d+)',
                r'([\d,]+[KMB]?)\s+(?:people\s+)?(?:like|follow)\s+this',
                r'([\d,]+)\s+(?:followers?|likes?|members?)',
                r'"subscriberCountText":"([^"]+)"',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, html, re.I)
                for match in matches:
                    parsed = parse_count(str(match).replace(",", ""))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        if is_group:
                            count = format_count(parsed, "members")
                            count_label = "members"
                        else:
                            count = format_count(parsed, "followers")
                            count_label = "followers"
                        print(f"✓ Found count from desktop: {count}")
                        break
                if count != "Hidden":
                    break
            
            # Bio
            bio = ""
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                bio = og_desc.get("content", "")
                bio = re.sub(r'\d+(?:,\d{3})*\s+(?:people\s+)?(?:like|follow).*', '', bio, flags=re.I)
                bio = bio.strip()[:250]
            
            if count != "Hidden" or name != identifier.replace("_", " ").title():
                print(f"✓ Desktop success for {identifier}")
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
    
    # Strategy 3: Mobile fallback
    try:
        print(f"Trying m.facebook.com for {identifier}...")
        mobile_url = base_url.replace("www.facebook.com", "m.facebook.com").replace("facebook.com", "m.facebook.com")
        
        r = session.get(mobile_url, timeout=15)
        
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            name = identifier.replace("_", " ").replace("-", " ").title()
            h_tags = soup.find_all(["h1", "h2", "h3"])
            for h in h_tags:
                text = h.get_text(strip=True)
                if len(text) > 3 and len(text) < 100:
                    name = text
                    break
            
            avatar = f"https://unavatar.io/facebook/{identifier}"
            img = soup.find("img", alt=lambda x: x and name.lower() in str(x).lower())
            if img and img.get("src") and img["src"].startswith("http"):
                avatar = img["src"]
                print(f"✓ Found avatar from mobile: {avatar[:80]}...")
            
            return {
                "success": True,
                "platform": "facebook",
                "username": identifier,
                "name": name,
                "type": "Group" if entity_type == "group" else "Page",
                "count": "Hidden",
                "count_exact": "0",
                "count_label": "",
                "profile_url": base_url,
                "profile_image": avatar,
                "bio": "Limited info from mobile view",
                "method": "facebook_mobile",
            }
    except Exception as e:
        print(f"Mobile failed: {e}")
    
    # Final fallback
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
                "bio": "Profile exists. Facebook requires login for detailed statistics.",
                "method": "unavatar",
                "note": "Facebook restricts public access. Consider using Facebook Graph API."
            }
    except:
        pass
    
    return {
        "success": False,
        "error": f"Cannot access Facebook {entity_type}: {identifier}. May be private or require login."
    }


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
        "version": "15.0 - Instagram & Facebook FIXED",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/instagram": "POST – Instagram (HD avatar + multiple methods)",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram (real photo)",
            "/api/facebook": "POST – Facebook (real name & avatar + mbasic)",
            "/api/health": "GET – health check",
        },
        "notes": {
            "instagram": "Uses official API + web scraping + Imginn for HD profile pictures",
            "facebook": "Uses mbasic.facebook.com for best success rate + real avatar extraction"
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
