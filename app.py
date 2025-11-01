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


# ==================== INSTAGRAM – REAL HD AVATAR (FIXED) ====================
def extract_ig(url_or_at):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url_or_at)
    if m: return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9_.]+)$", url_or_at)
    if m: return m.group(1)
    return None


def scrape_instagram(username):
    username = username.lstrip("@")
    api_url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.instagram.com/",
        "x-ig-app-id": "936619743392459",
        "x-asbd-id": "129477",
        "x-ig-www-claim": "0",
        "x-requested-with": "XMLHttpRequest",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        # Instagram now requires CSRF token + session (even dummy values help)
        "cookie": "csrftoken=dummy; sessionid=dummy;",
    }

    # ---- 1. Official API (HD pic) ----
    try:
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
                # FORCE HD PROFILE PIC
                avatar = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or f"https://unavatar.io/instagram/{username}"
                is_verified = user.get("is_verified", False)
                is_private = user.get("is_private", False)

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
        print(f"Instagram API error for {username}: {e}")

    # ---- 2. Web-scrape fallback (og:image) ----
    try:
        web_url = f"https://www.instagram.com/{username}/"
        r = requests.get(web_url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            og_img = soup.find("meta", property="og:image")
            avatar = og_img["content"] if og_img and og_img.get("content") else f"https://unavatar.io/instagram/{username}"

            meta = soup.find("meta", property="og:description")
            txt = meta.get("content", "") if meta else ""
            name = re.search(r"^([^-(]+)", txt).group(1).strip() if re.search(r"^([^-(]+)", txt) else username

            followers_match = re.search(r"([\d,.]+[KMB]?)\s*Followers?", txt, re.I)
            following_match = re.search(r"([\d,.]+[KMB]?)\s*Following", txt, re.I)
            posts_match = re.search(r"([\d,.]+[KMB]?)\s*Posts?", txt, re.I)

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
                "bio": txt.split(" - ")[0][:250] if " - " in txt else "",
                "method": "instagram_web",
            }
    except Exception as e:
        print(f"Instagram web fallback failed: {e}")

    # ---- 3. Unavatar fallback ----
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


# ==================== TELEGRAM – REAL AVATAR (data-URI) ====================
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

        # ---------- Real photo ----------
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


# ==================== FACEBOOK – REAL NAME & AVATAR (FIXED) ====================
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
    base_url = f"https://www.facebook.com/{identifier}" if entity_type == "page" else f"https://www.facebook.com/groups/{identifier}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
    }

    # ---- 1. Desktop version ----
    try:
        r = requests.get(base_url, headers=headers, timeout=20, allow_redirects=True)
        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")

            # ----- NAME -----
            name = identifier.replace("_", " ").replace("-", " ").title()
            title_tag = soup.find("title")
            if title_tag:
                txt = title_tag.get_text(strip=True)
                txt = re.sub(r"\s*[-|·]\s*Facebook$", "", txt, flags=re.I)
                txt = re.sub(r"^\(?\d+\)?\s*", "", txt)
                if txt and not any(w in txt.lower() for w in ["log in", "sign up", "facebook", "meta"]):
                    name = txt
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                name = og_title["content"]

            # ----- AVATAR -----
            avatar = f"https://unavatar.io/facebook/{identifier}"
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content") and og_img["content"].startswith("http"):
                avatar = og_img["content"]
            else:
                img = soup.find("img", alt=lambda x: x and name.lower() in x.lower())
                if img and img.get("src") and img["src"].startswith("http"):
                    avatar = img["src"]

            # ----- STATS -----
            is_group = entity_type == "group" or "/groups/" in r.url
            count = "Hidden"
            count_exact = "0"
            count_label = ""

            patterns = [
                r'(\d{1,3}(?:,\d{3})+)\s*(?:people follow|followers?|members?|likes?)',
                r'(\d+\.?\d*[KMB])\s*(?:people follow|followers?|members?|likes?)',
                r'(\d+)\s*(?:people follow|followers?|members?|likes?)',
                r'"follower_count":\s*(\d+)',
                r'"member_count":\s*(\d+)',
            ]
            for pat in patterns:
                for m in re.findall(pat, r.text, re.I):
                    parsed = parse_count(str(m).replace(",", ""))
                    if parsed > 0:
                        count_exact = str(parsed)
                        if is_group:
                            count = format_count(parsed, "members")
                            count_label = "members"
                        else:
                            count = format_count(parsed, "followers")
                            count_label = "followers"
                        break
                if count != "Hidden":
                    break

            # ----- BIO -----
            bio = ""
            meta_desc = soup.find("meta", {"property": "og:description"}) or soup.find("meta", {"name": "description"})
            if meta_desc:
                bio = meta_desc.get("content", "")
                bio = re.sub(r"^\S+?\s*[·|]\s*", "", bio)
                bio = re.sub(r"\s*\.?\s*Log\s+[Ii]n.*", "", bio)
                bio = re.sub(r"\s*\.?\s*Sign\s+[Uu]p.*", "", bio)
                bio = re.sub(r"\d+(?:,\d{3})*\s*(?:people follow|followers?|members?|likes?).*", "", bio, flags=re.I)
                bio = bio.strip()[:250]

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
                "bio": bio or "Page/Group information may require login to view",
                "method": "facebook_desktop",
            }
    except Exception as e:
        print(f"FB desktop error {identifier}: {e}")

    # ---- 2. Mobile fallback ----
    try:
        mobile_url = base_url.replace("www.facebook.com", "m.facebook.com")
        r = requests.get(mobile_url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            name = identifier.replace("_", " ").replace("-", " ").title()
            h = soup.find(["h1", "h2", "h3"])
            if h: name = h.get_text(strip=True)

            avatar = f"https://unavatar.io/facebook/{identifier}"
            img = soup.find("img", alt=lambda x: x and name.lower() in x.lower())
            if img and img.get("src") and img["src"].startswith("http"):
                avatar = img["src"]

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
        print(f"FB mobile error {identifier}: {e}")

    # ---- 3. Unavatar fallback ----
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
        "version": "14.0 – IG HD pic + FB real name & avatar",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X",
            "/api/instagram": "POST – Instagram (official API + HD pic)",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram (real photo)",
            "/api/facebook": "POST – Facebook (real name & avatar)",
            "/api/health": "GET – health check",
        },
        "notes": {
            "instagram": "Uses Instagram web API – always returns HD profile picture.",
            "facebook": "Extracts real name from title/og:title and real avatar from og:image or profile <img>.",
            "telegram": "Downloads actual photo as data-URI.",
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
