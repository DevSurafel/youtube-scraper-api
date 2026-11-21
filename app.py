# --------------------------------------------------------------
# app.py – Social-Media Stats API v29 (FLEXIBLE LINKS + RETRIES)
# --------------------------------------------------------------


import os
import re
import json
import asyncio
import base64
import random
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, Response
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
BASE_URL = os.getenv("BASE_URL", "")
# Optional: a residential/rotating proxy, e.g. http://user:pass@host:port  (Instagram and X block most
# datacenter IPs such as Render, so a proxy is the single biggest reliability boost if lookups still fail)
SCRAPER_PROXY = os.getenv("SCRAPER_PROXY", "")
PROXIES = {"http": SCRAPER_PROXY, "https": SCRAPER_PROXY} if SCRAPER_PROXY else None
ALLOWED_IMAGE_HOSTS = (
    "twimg.com", "cdninstagram.com", "fbcdn.net", "instagram.com", "tiktokcdn.com",
    "tiktokcdn-us.com", "unavatar.io", "ggpht.com", "googleusercontent.com", "nitter.net",
)


def http_get(url, **kw):
    """requests.get that goes through SCRAPER_PROXY when configured."""
    kw.setdefault("timeout", 15)
    if PROXIES:
        kw.setdefault("proxies", PROXIES)
    return requests.get(url, **kw)


# ------------------------------------------------
def async_route(f):
    @wraps(f)
    def wrapper(*a, **kw):
        return asyncio.run(f(*a, **kw))
    return wrapper


def get_base_url():
    """Get the base URL for the application"""
    if BASE_URL:
        return BASE_URL.rstrip('/')
    if request:
        return request.url_root.rstrip('/')
    return ""


# ==================== LINK PARSING ====================
from urllib.parse import urlparse, parse_qs, unquote

PLATFORM_HOSTS = {
    "instagram": ("instagram.com", "instagr.am"),
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "tiktok": ("tiktok.com",),
    "twitter": ("twitter.com", "x.com"),
    "facebook": ("facebook.com", "fb.com", "fb.me", "fb.watch"),
    "telegram": ("t.me", "telegram.me", "telegram.dog"),
}


def parse_link(raw):
    """Accepts a link in almost any form (no https, mobile host, extra path, tracking params, tg://)
    and returns (host, [path segments], {query}). Host has www./m./mobile./web. removed."""
    raw = (raw or "").strip()
    m = re.match(r"^tg://resolve\?domain=([A-Za-z0-9_]+)", raw, re.I)
    if m:
        return "t.me", [m.group(1)], {}
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    try:
        u = urlparse(raw)
    except Exception:
        return "", [], {}
    host = (u.hostname or "").lower()
    for pre in ("www.", "m.", "mobile.", "web.", "mbasic.", "touch."):
        if host.startswith(pre):
            host = host[len(pre):]
    segs = [unquote(x) for x in u.path.split("/") if x]
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    return host, segs, q


def host_matches(host, platform):
    return any(host == h or host.endswith("." + h) for h in PLATFORM_HOSTS[platform])


def detect_platform(raw):
    host, _, _ = parse_link(raw)
    for p in PLATFORM_HOSTS:
        if host_matches(host, p):
            return p
    return None


def with_retry(fn, attempts=2, delay=1.2):
    """Run a scraper again when it fails: these sites fail randomly."""
    import time
    res = None
    for i in range(attempts):
        try:
            res = fn()
        except Exception as e:
            print(f"  [retry] attempt {i+1} crashed: {e}")
            res = {"success": False, "error": str(e)}
        if res and res.get("success"):
            return res
        if i < attempts - 1:
            time.sleep(delay)
    return res


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


# ==================== IMAGE PROXY ====================
@app.route("/api/proxy-image", methods=["GET"])
def proxy_image():
    """Proxy images to avoid CORS issues"""
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if urlparse(url).scheme not in ("http", "https") or not any(host == h or host.endswith("." + h) for h in ALLOWED_IMAGE_HOSTS):
            return jsonify({"error": "Image host not allowed"}), 400
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": url.split("/pic/")[0] if "/pic/" in url else url,
        }

        r = http_get(url, headers=headers, timeout=10)
        
        if r.status_code != 200:
            return jsonify({"error": "Failed to fetch image"}), r.status_code
        
        content_type = r.headers.get('Content-Type', 'image/jpeg')
        
        return Response(
            r.content,
            mimetype=content_type,
            headers={
                'Cache-Control': 'public, max-age=86400',
                'Access-Control-Allow-Origin': '*'
            }
        )
    except Exception as e:
        print(f"[Proxy] Error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== YOUTUBE ====================
def extract_yt(url):
    """Returns (value, type) where type is id | handle | user | custom.
    Handles /channel/UC..., /@handle, /c/name, /user/name, bare /name, trailing /videos /about etc."""
    host, segs, q = parse_link(url)
    if url.strip().startswith("@") and "/" not in url:
        return url.strip().lstrip("@"), "handle"
    if not host_matches(host, "youtube") or not segs:
        return None, None
    first = segs[0]
    if first.startswith("@") and len(first) > 1:
        return first[1:], "handle"
    if first == "channel" and len(segs) > 1:
        return segs[1], "id"
    if first == "c" and len(segs) > 1:
        return segs[1], "custom"
    if first == "user" and len(segs) > 1:
        return segs[1], "user"
    if first not in {"watch", "playlist", "results", "feed", "shorts", "live", "embed", "channel", "c", "user", "@"} and not host.startswith("youtu.be"):
        return first, "custom"  # youtube.com/SomeName
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
        "isVerified": snippet.get("customUrl") is not None,
    }


def _yt_first_channel_id(params):
    r = http_get("https://www.googleapis.com/youtube/v3/channels", params={**params, "part": "id", "key": YOUTUBE_API_KEY}, timeout=10).json()
    items = r.get("items") or []
    return items[0]["id"] if items else None


def yt_by_handle(handle, verification_code=None):
    if not YOUTUBE_API_KEY:
        return {"success": False, "error": "YouTube API key missing"}
    h = handle.lstrip("@")
    cid = _yt_first_channel_id({"forHandle": "@" + h})  # exact, official lookup
    if not cid:
        r = http_get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"part": "snippet", "q": h, "type": "channel", "key": YOUTUBE_API_KEY, "maxResults": 1},
            timeout=10,
        ).json()
        if not r.get("items"):
            return {"success": False, "error": "Channel not found"}
        cid = r["items"][0]["snippet"]["channelId"]
    return yt_by_id(cid, verification_code)


def yt_by_username(name, verification_code=None):
    """Old-style /user/name links."""
    if not YOUTUBE_API_KEY:
        return {"success": False, "error": "YouTube API key missing"}
    cid = _yt_first_channel_id({"forUsername": name})
    if not cid:
        return yt_by_handle(name, verification_code)
    return yt_by_id(cid, verification_code)


def yt_by_custom(name, verification_code=None):
    """/c/name or youtube.com/name: try it as a handle first, then as an old username, then search."""
    res = yt_by_handle(name, verification_code)
    if res.get("success"):
        return res
    return yt_by_username(name, verification_code)


def yt_lookup(value, typ, verification_code=None):
    if typ == "id":
        return yt_by_id(value, verification_code)
    if typ == "handle":
        return yt_by_handle(value, verification_code)
    if typ == "user":
        return yt_by_username(value, verification_code)
    return yt_by_custom(value, verification_code)


# ==================== TWITTER ====================
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.kavin.rocks",
    "https://nitter.unixfox.eu",
    "https://nitter.1d4.us",
    "https://nitter.esmailelbob.xyz",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

def _random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

TW_RESERVED = {"home", "explore", "i", "search", "settings", "intent", "share", "hashtag", "messages",
               "notifications", "login", "signup", "tos", "privacy", "compose", "about", "download"}


def extract_tw(url_or_at):
    raw = (url_or_at or "").strip()
    m = re.match(r"^@?([A-Za-z0-9_]{1,15})$", raw)
    if m:
        return m.group(1)
    host, segs, _ = parse_link(raw)
    if not host_matches(host, "twitter") and host not in ("fxtwitter.com", "vxtwitter.com", "nitter.net"):
        return None
    if not segs:
        return None
    first = segs[0].lstrip("@")
    if first.lower() in TW_RESERVED:
        return None
    return first if re.match(r"^[A-Za-z0-9_]{1,15}$", first) else None


# ---------- NEW: Twitter syndication endpoint (no login, no scraping) ----------
def scrape_twitter_syndication(username, verification_code=None):
    """Most reliable Twitter/X method in 2025 — public syndication widget."""
    username = username.lstrip("@").strip()
    if not username:
        return None
    try:
        url = f"https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={username}"
        r = http_get(
            url,
            headers={"User-Agent": random.choice(USER_AGENTS)},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"  [Twitter syndication] HTTP {r.status_code}")
            return None
        data = r.json()
        if not data or not isinstance(data, list):
            print(f"  [Twitter syndication] Empty/invalid response")
            return None
        u = data[0]
        followers = int(u.get("followers_count", 0) or 0)

        # Get full bio via oembed (syndication doesn't return it)
        bio = ""
        try:
            oembed = requests.get(
                f"https://publish.twitter.com/oembed?url=https://twitter.com/{username}",
                timeout=8,
                headers={"User-Agent": random.choice(USER_AGENTS)},
            ).json()
            bio = re.sub(r"<[^>]+>", "", oembed.get("description", "") or "")[:300]
        except Exception:
            pass

        verified = False
        if verification_code and bio:
            verified = check_verification_code(bio, verification_code)

        avatar_raw = (u.get("profile_image_url_https") or "").replace("_normal", "_400x400")
        from urllib.parse import quote
        app_base = get_base_url()
        if avatar_raw:
            avatar = f"{app_base}/api/proxy-image?url={quote(avatar_raw)}"
        else:
            avatar = f"https://unavatar.io/twitter/{username}"

        print(f"  [Twitter syndication] SUCCESS: {u.get('name', username)} | {format_count(followers, 'followers')}")

        return {
            "success": True,
            "platform": "twitter",
            "username": username,
            "name": u.get("name", username),
            "bio": bio,
            "follower_count": format_count(followers, "followers"),
            "follower_count_exact": str(followers),
            "following_count": "Hidden",
            "tweet_count": "Hidden",
            "profile_url": f"https://twitter.com/{username}",
            "profile_image": avatar,
            "method": "syndication",
            "verificationCodeFound": verified,
            "isVerified": bool(u.get("verified", False)),
        }
    except Exception as e:
        print(f"  [Twitter syndication] Error: {e}")
        return None


def _proxy_avatar(raw, fallback):
    from urllib.parse import quote
    if raw and raw.startswith("http"):
        return f"{get_base_url()}/api/proxy-image?url={quote(raw)}"
    return fallback


def scrape_twitter_fx(username, verification_code=None):
    """FixTweet public API: returns followers, bio and avatar as JSON, no login."""
    try:
        r = http_get(f"https://api.fxtwitter.com/{username}", headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=12)
        if r.status_code != 200:
            print(f"  [Twitter fx] HTTP {r.status_code}")
            return None
        u = (r.json() or {}).get("user")
        if not u:
            return None
        followers = int(u.get("followers") or 0)
        bio = (u.get("description") or "")[:300]
        verification = u.get("verification") or {}
        avatar_raw = (u.get("avatar_url") or "").replace("_normal", "_400x400")
        return {
            "success": True,
            "platform": "twitter",
            "username": u.get("screen_name") or username,
            "name": u.get("name") or username,
            "bio": bio,
            "follower_count": format_count(followers, "followers"),
            "follower_count_exact": str(followers),
            "following_count": format_count(u.get("following") or 0, "following"),
            "tweet_count": str(u.get("tweets") or 0),
            "likes_count": str(u.get("likes") or 0),
            "profile_url": f"https://twitter.com/{username}",
            "profile_image": _proxy_avatar(avatar_raw, f"https://unavatar.io/twitter/{username}"),
            "method": "fxtwitter",
            "verificationCodeFound": bool(verification_code and check_verification_code(bio, verification_code)),
            "isVerified": bool(verification.get("verified")),
        }
    except Exception as e:
        print(f"  [Twitter fx] Error: {e}")
        return None


def scrape_twitter_timeline(username, verification_code=None):
    """Public embedded-timeline page; its __NEXT_DATA__ holds the profile."""
    try:
        r = http_get(
            f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}",
            headers={"User-Agent": random.choice(USER_AGENTS), "Accept": "text/html"},
            timeout=12,
        )
        if r.status_code != 200:
            print(f"  [Twitter timeline] HTTP {r.status_code}")
            return None
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', r.text, re.S)
        if not m:
            return None
        data = json.loads(m.group(1))
        entries = data["props"]["pageProps"]["timeline"]["entries"]
        user = None
        for e in entries:
            user = (((e.get("content") or {}).get("tweet") or {}).get("user"))
            if user:
                break
        if not user:
            return None
        followers = int(user.get("followers_count") or 0)
        bio = (user.get("description") or "")[:300]
        avatar_raw = (user.get("profile_image_url_https") or "").replace("_normal", "_400x400")
        return {
            "success": True,
            "platform": "twitter",
            "username": user.get("screen_name") or username,
            "name": user.get("name") or username,
            "bio": bio,
            "follower_count": format_count(followers, "followers"),
            "follower_count_exact": str(followers),
            "following_count": format_count(user.get("friends_count") or 0, "following"),
            "tweet_count": str(user.get("statuses_count") or 0),
            "profile_url": f"https://twitter.com/{username}",
            "profile_image": _proxy_avatar(avatar_raw, f"https://unavatar.io/twitter/{username}"),
            "method": "syndication_timeline",
            "verificationCodeFound": bool(verification_code and check_verification_code(bio, verification_code)),
            "isVerified": bool(user.get("verified") or user.get("is_blue_verified")),
        }
    except Exception as e:
        print(f"  [Twitter timeline] Error: {e}")
        return None


def scrape_twitter(username, verification_code=None):
    username = username.lstrip("@").strip()
    if not username:
        return {"success": False, "error": "Empty username"}

    # STRATEGY A: FixTweet JSON API, then embedded timeline page
    for strategy in (scrape_twitter_fx, scrape_twitter_timeline):
        res = strategy(username, verification_code)
        if res:
            return res

    # ⭐ STRATEGY 0: syndication (most reliable, no login)
    synd = scrape_twitter_syndication(username, verification_code)
    if synd:
        return synd

    # STRATEGY 1: Nitter instances (kept as fallback)
    base = NITTER_INSTANCES[0]  # ← default, prevents NameError in avatar fallback
    instances = NITTER_INSTANCES.copy()
    random.shuffle(instances)

    for base in instances:
        try:
            url = f"{base}/{username}"
            print(f"[Twitter] Trying {base} for @{username}...")
            r = http_get(url, headers=_random_headers(), timeout=12, allow_redirects=True)

            if "Just a moment..." in r.text or "Checking your browser" in r.text:
                print("  Cloudflare challenge → skip")
                continue

            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            txt = r.text.lower()
            if any(err in txt for err in ["user not found", "doesn't exist", "suspended", "account suspended"]):
                continue

            name = username
            name_selectors = [
                "div.profile-card-fullname",
                "h1.profile-fullname",
                "a.profile-card-fullname",
                "div.profile-fullname",
                "span.profile-fullname",
                "h1",
            ]
            for sel in name_selectors:
                tag = soup.select_one(sel)
                if tag and tag.get_text(strip=True):
                    candidate = tag.get_text(strip=True).strip()
                    if candidate and candidate != username and len(candidate) > 1:
                        name = candidate
                        break

            if not name or name == username:
                title_tag = soup.find("title")
                if title_tag:
                    title_text = title_tag.get_text(strip=True)
                    match = re.match(r"^(.+?)(?:\s+[@/\\(]|\s+on\s+X)", title_text, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()

            bio_tag = soup.find("p", class_="profile-bio") or soup.find("div", class_="profile-bio")
            bio = bio_tag.get_text(strip=True) if bio_tag else ""

            verified_code = False
            if verification_code and bio:
                verified_code = check_verification_code(bio, verification_code)

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
                            h = header.get_text(strip=True).lower()
                            v = value.get_text(strip=True)
                            if "post" in h or "tweet" in h:
                                tweets = v
                            elif "following" in h:
                                following = v
                            elif "follower" in h:
                                followers = v

            follower_cnt = parse_count(followers)

            avatar_raw = None
            
            avatar_selectors = [
                "a.profile-card-avatar img",
                "img.profile-card-avatar",
                "div.profile-avatar img",
                "img.avatar",
                f"a[href^='/{username}'] img",
                "img[src*='profile_images']",
                "img[data-src*='profile_images']",
            ]
            
            for sel in avatar_selectors:
                img = soup.select_one(sel)
                if img:
                    src = img.get("src") or img.get("data-src")
                    if src:
                        if src.startswith("http://") or src.startswith("https://"):
                            avatar_raw = src
                            break
                        elif src.startswith("//"):
                            avatar_raw = "https:" + src
                            break
                        elif src.startswith("/pic/") or src.startswith("/profile_images/"):
                            avatar_raw = base + src
                            print(f"  [Avatar] Converted: {src} → {avatar_raw}")
                            break
                        elif src.startswith("/"):
                            avatar_raw = base + src
                            break

            if not avatar_raw:
                for img in soup.find_all("img"):
                    src = img.get("src") or img.get("data-src") or ""
                    if "profile_images" in src or "pbs.twimg.com" in src:
                        if src.startswith("http"):
                            avatar_raw = src
                        elif src.startswith("//"):
                            avatar_raw = "https:" + src
                        elif src.startswith("/"):
                            avatar_raw = base + src
                        if avatar_raw:
                            print(f"  [Avatar] Found via fallback: {avatar_raw[:80]}...")
                            break

            if avatar_raw and avatar_raw.startswith("http"):
                from urllib.parse import quote
                app_base = get_base_url()
                avatar = f"{app_base}/api/proxy-image?url={quote(avatar_raw)}"
                print(f"  [Avatar] Proxied (Full URL): {avatar_raw[:60]}... → {avatar[:80]}...")
            else:
                avatar = f"https://unavatar.io/twitter/{username}"

            is_verified = bool(soup.find("span", class_="icon-verified"))

            if follower_cnt >= 10 or (name != username and name) or bio:
                print(f"  SUCCESS: '{name}' | {format_count(follower_cnt, 'followers')}")
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
                    "verificationCodeFound": verified_code,
                    "isVerified": is_verified,
                }

        except Exception as e:
            print(f"  Error on {base}: {e}")
            continue

    # No source could read the profile. Do NOT pretend success: the site then lets the seller enter the
    # details manually and prove ownership with a screenshot.
    return {"success": False, "error": f"Could not read @{username} right now. Enter the details manually."}


# ==================== INSTAGRAM (FIXED) ====================
IG_RESERVED = {"p", "reel", "reels", "explore", "accounts", "direct", "tv", "about", "web", "developer", "legal", "challenge"}


def extract_ig(url_or_at):
    raw = (url_or_at or "").strip()
    m = re.match(r"^@?([A-Za-z0-9_.]{1,30})$", raw)
    if m and detect_platform(raw) is None:
        return m.group(1)
    host, segs, _ = parse_link(raw)
    if not host_matches(host, "instagram") or not segs:
        return m.group(1) if m else None
    first = segs[0].lstrip("@")
    if first == "stories" and len(segs) > 1:
        first = segs[1]
    if first.lower() in IG_RESERVED:
        return None
    return first if re.match(r"^[A-Za-z0-9_.]{1,30}$", first) else None


# ---------- NEW: Instagram API helper (no login required, tries both hosts) ----------
def _format_ig_user(user, username, verification_code):
    bio = user.get("biography", "") or ""
    verified = bool(verification_code and check_verification_code(bio, verification_code))

    img = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or ""
    from urllib.parse import quote
    app_base = get_base_url()
    if img and img.startswith("http"):
        img = f"{app_base}/api/proxy-image?url={quote(img)}"
    else:
        img = f"https://unavatar.io/instagram/{username}"

    followers = user.get("edge_followed_by", {}).get("count", 0)
    following = user.get("edge_follow", {}).get("count", 0)
    posts = user.get("edge_owner_to_timeline_media", {}).get("count", 0)

    return {
        "success": True,
        "platform": "instagram",
        "username": username,
        "name": user.get("full_name") or username,
        "follower_count": format_count(followers, "followers"),
        "follower_count_exact": str(followers),
        "following_count": format_count(following, "following"),
        "post_count": format_count(posts, "posts"),
        "profile_url": f"https://instagram.com/{username}",
        "profile_image": img,
        "bio": bio[:250],
        "method": "instagram_api",
        "verificationCodeFound": verified,
        "isVerified": user.get("is_verified", False),
    }


def scrape_instagram_api(username, verification_code=None):
    """Try Instagram's web_profile_info endpoint on both hosts."""
    username = username.lstrip("@").strip()
    if not username:
        return None

    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "x-ig-app-id": "936619743392459",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{username}/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }

    hosts = [
        "https://i.instagram.com",
        "https://www.instagram.com",
    ]

    for host in hosts:
        try:
            url = f"{host}/api/v1/users/web_profile_info/?username={username}"
            print(f"  [IG API] Trying {host}...")
            # dummy session cookie sometimes satisfies the gate
            r = http_get(
                url,
                headers=base_headers,
                cookies={"sessionid": "0", "csrftoken": "0"},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  [IG API] {host} → HTTP {r.status_code}")
                continue

            data = r.json()
            user = data.get("data", {}).get("user")
            if user:
                print(f"  [IG API] SUCCESS via {host}: {user.get('full_name', username)}")
                return _format_ig_user(user, username, verification_code)
            else:
                print(f"  [IG API] {host} → no user object")
        except Exception as e:
            print(f"  [IG API] {host} error: {e}")

    return None


def scrape_instagram_meta(username, verification_code=None):
    """Public profile page: og:description carries 'N Followers, N Following, N Posts - Name (@user) on Instagram: "bio"'."""
    from html import unescape
    uas = [
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        random.choice(USER_AGENTS),
    ]
    for ua in uas:
        try:
            r = http_get(
                f"https://www.instagram.com/{username}/",
                headers={"User-Agent": ua, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  [IG meta] {ua[:20]}... HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            og_desc = soup.find("meta", property="og:description")
            og_title = soup.find("meta", property="og:title")
            og_img = soup.find("meta", property="og:image")
            if not og_desc or not og_desc.get("content"):
                continue
            desc = unescape(og_desc["content"])
            m = re.search(r"([\d.,]+[KMB]?)\s+Followers?,\s+([\d.,]+[KMB]?)\s+Following,\s+([\d.,]+[KMB]?)\s+Posts?", desc, re.I)
            if not m:
                continue
            followers = parse_count(m.group(1).replace(",", ""))
            following = parse_count(m.group(2).replace(",", ""))
            posts = parse_count(m.group(3).replace(",", ""))
            bio = ""
            bm = re.search(r'on Instagram:\s*["\u201c](.*)["\u201d]\s*$', desc, re.S)
            if bm:
                bio = bm.group(1).strip()
            name = username
            if og_title and og_title.get("content"):
                tm = re.match(r"^(.*?)\s*\(@", unescape(og_title["content"]))
                if tm and tm.group(1).strip():
                    name = tm.group(1).strip()
            img_raw = og_img.get("content") if og_img else ""
            print(f"  [IG meta] SUCCESS: {name} | {followers}")
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
                "profile_image": _proxy_avatar(img_raw, f"https://unavatar.io/instagram/{username}"),
                "bio": bio[:250],
                "method": "instagram_meta",
                "verificationCodeFound": bool(verification_code and check_verification_code(bio, verification_code)),
                "isVerified": False,
            }
        except Exception as e:
            print(f"  [IG meta] error: {e}")
    return None


def scrape_instagram(username, verification_code=None):
    username = username.lstrip("@").strip()
    print(f"[Instagram] Scraping @{username}...")

    # STRATEGY 0a: public profile meta tags (works for many public accounts without login)
    meta_result = scrape_instagram_meta(username, verification_code)
    if meta_result:
        return meta_result

    # ⭐ STRATEGY 0: Instagram API (both hosts)
    api_result = scrape_instagram_api(username, verification_code)
    if api_result:
        return api_result

    # Nothing could read the profile (Instagram blocks most server IPs). Do NOT pretend success:
    # the site then lets the seller enter the details manually and prove ownership with a screenshot.
    print(f"  FAILED: all strategies failed for @{username}")
    return {"success": False, "error": f"Could not read @{username} right now. Enter the details manually."}


# ==================== TIKTOK ====================
def extract_tt(url_or_at):
    raw = (url_or_at or "").strip()
    m = re.match(r"^@?([A-Za-z0-9_.]{2,24})$", raw)
    if m:
        return m.group(1)
    host, segs, _ = parse_link(raw)
    if host in ("vm.tiktok.com", "vt.tiktok.com") or (host_matches(host, "tiktok") and segs and segs[0] == "t"):
        try:  # short share link: follow the redirect to the real profile or video link
            r = http_get(raw if raw.startswith("http") else "https://" + raw, allow_redirects=True, timeout=10,
                         headers={"User-Agent": random.choice(USER_AGENTS)})
            host, segs, _ = parse_link(r.url)
        except Exception:
            return None
    if not host_matches(host, "tiktok") or not segs:
        return None
    first = segs[0]
    if first.startswith("@"):
        return first[1:] or None
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
    raw = (url_or_at or "").strip()
    m = re.match(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$", raw)
    if m:
        return m.group(1)
    m = re.match(r"^(-100\d+)$", raw)
    if m:
        return m.group(1)
    host, segs, _ = parse_link(raw)
    if not host_matches(host, "telegram") or not segs:
        return None
    if segs[0] == "s" and len(segs) > 1:  # t.me/s/channel (web preview)
        segs = segs[1:]
    first = segs[0].lstrip("@")
    if first.startswith("+") or first.lower() in {"joinchat", "c", "addstickers", "share", "iv"}:
        return None  # private invite or message link, cannot be looked up
    return first if re.match(r"^[A-Za-z][A-Za-z0-9_]{3,31}$", first) else None


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
FB_RESERVED = {"watch", "marketplace", "sharer", "sharer.php", "share", "login", "events", "gaming", "reel", "stories", "photo", "photos", "story.php", "permalink.php", "hashtag", "help", "policies"}


def extract_fb(url_or_at):
    """Returns (identifier, type) where type is page | group | profile."""
    raw = (url_or_at or "").strip()
    m = re.match(r"^@?([A-Za-z0-9.\-_]{3,})$", raw)
    if m:
        return m.group(1), "page"
    host, segs, q = parse_link(raw)
    if not host_matches(host, "facebook"):
        return None, None
    if not segs:
        return None, None
    first = segs[0]
    if first == "profile.php":
        return (q.get("id"), "page") if q.get("id") else (None, "profile")  # pages without a username use ?id=
    if first == "people" and len(segs) > 2:
        return segs[2], "page"  # /people/Name/<numeric id>
    if first == "people":
        return None, "profile"
    if first == "groups" and len(segs) > 1:
        return segs[1], "group"
    if first == "pages" and len(segs) > 1:
        return (segs[2] if len(segs) > 2 else segs[1]), "page"  # /pages/Name/12345 -> numeric id
    if first == "p" and len(segs) > 1:
        return segs[1], "page"
    if first.lower() in FB_RESERVED:
        return None, None
    return first, "page"


def scrape_facebook(identifier, entity_type="page", verification_code=None):
    if entity_type == "profile":
        return {"success": False, "error": "Personal profiles not supported"}

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
            return {"success": False, "error": "Personal profile"}

        if r.status_code == 200 and "login" not in r.url.lower():
            soup = BeautifulSoup(r.text, "html.parser")

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

            avatar = f"https://unavatar.io/facebook/{identifier}"
            for img in soup.find_all("img", limit=10):
                src = img.get("src", "")
                if "scontent" in src or ("fbcdn" in src and "https://" in src):
                    avatar = src
                    break

            bio = ""
            meta_desc = soup.find("meta", {"name": "description"})
            if meta_desc:
                bio = meta_desc.get("content", "")[:300]

            verified = False
            if verification_code and bio:
                verified = check_verification_code(bio, verification_code)

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

            print(f"  SUCCESS: {name}, {count}")

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

            name = identifier.replace("_", " ").replace("-", " ").title()
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                name = re.sub(r"\s*[-|]\s*Facebook\s*$", "", og_title["content"], flags=re.I).strip()

            avatar = f"https://unavatar.io/facebook/{identifier}"
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                avatar = og_img["content"]

            bio = ""
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                bio = og_desc.get("content", "")[:300]

            verified = False
            if verification_code and bio:
                verified = check_verification_code(bio, verification_code)

            is_group = entity_type == "group" or "/groups/" in r.url
            count_label = "members" if is_group else "followers"
            count = "Hidden"
            count_exact = "0"

            json_patterns = [
                r'"follower_count":(\d+)',
                r'"member_count":(\d+)',
                r'"fan_count":(\d+)',
                r'"subscriber_count":(\d+)',
            ]
            for pattern in json_patterns:
                match = re.search(pattern, r.text)
                if match:
                    parsed = int(match.group(1))
                    if parsed >= 10:
                        count_exact = str(parsed)
                        count = format_count(parsed, count_label)
                        break

            if count == "Hidden":
                text_patterns = [
                    r'([\d,]+)\s+(?:people|members?)\s+(?:like|follow)',
                    r'([\d,.]+[KMB]?)\s+followers?',
                    r'([\d,.]+[KMB]?)\s+members?',
                ]
                for pattern in text_patterns:
                    match = re.search(pattern, r.text, re.I)
                    if match:
                        parsed = parse_count(match.group(1).replace(",", ""))
                        if parsed >= 10:
                            count_exact = str(parsed)
                            count = format_count(parsed, count_label)
                            break

            print(f"  SUCCESS desktop Found: {name}, {count}")

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
                "method": "facebook_desktop",
                "verificationCodeFound": verified,
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
                "verificationCodeFound": False,
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
    url = (payload.get("url") or "").strip()
    verification_code = payload.get("verificationCode", "")
    hint = (payload.get("platform") or "").lower()

    if not url:
        return jsonify({"success": False, "error": "Missing url"}), 400

    platform = detect_platform(url) or (hint if hint in PLATFORM_HOSTS else None)
    if not platform and url.startswith("@"):
        platform = None  # unknown platform handle: try all below
    elif not platform and re.match(r"^[A-Za-z0-9_.]+$", url):
        platform = hint if hint in PLATFORM_HOSTS else None

    if platform == "youtube":
        if not YOUTUBE_API_KEY:
            return jsonify({"success": False, "error": "YouTube API key missing"}), 500
        val, typ = extract_yt(url)
        if not val:
            return jsonify({"success": False, "error": "Invalid YouTube link. Use a channel link such as youtube.com/@name or youtube.com/channel/UC..."}), 400
        return jsonify(with_retry(lambda: yt_lookup(val, typ, verification_code)))

    if platform == "twitter":
        user = extract_tw(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid X/Twitter link. Use x.com/username"}), 400
        return jsonify(with_retry(lambda: scrape_twitter(user, verification_code), attempts=3))

    if platform == "instagram":
        user = extract_ig(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid Instagram link. Use instagram.com/username"}), 400
        return jsonify(with_retry(lambda: scrape_instagram(user, verification_code), attempts=3))

    if platform == "tiktok":
        user = extract_tt(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid TikTok link. Use tiktok.com/@username"}), 400
        return jsonify(with_retry(lambda: scrape_tiktok(user, verification_code)))

    if platform == "telegram":
        user = extract_tg(url)
        if not user:
            return jsonify({"success": False, "error": "Invalid Telegram link. Use t.me/username (private invite links cannot be checked)"}), 400
        return jsonify(with_retry(lambda: scrape_telegram(user, verification_code)))

    if platform == "facebook":
        ident, etype = extract_fb(url)
        if etype == "profile":
            return jsonify({"success": False, "error": "Personal Facebook profiles are not supported, only Pages and Groups"}), 400
        if not ident:
            return jsonify({"success": False, "error": "Invalid Facebook link"}), 400
        return jsonify(with_retry(lambda: scrape_facebook(ident, etype, verification_code)))

    if url.startswith("@"):
        user = url.lstrip("@")
        for scraper in (
            lambda u: scrape_twitter(u, verification_code),
            lambda u: scrape_instagram(u, verification_code),
            lambda u: scrape_tiktok(u, verification_code),
            lambda u: scrape_telegram(u, verification_code),
            lambda u: scrape_facebook(u, "page", verification_code)
        ):
            res = scraper(user)
            if res.get("success"):
                return jsonify(res)

    return jsonify({"success": False, "error": "Unsupported platform or invalid link"}), 400


# Platform-specific endpoints
@app.route("/api/youtube", methods=["POST"])
def youtube_endpoint():
    payload = request.get_json() or {}
    url = payload.get("url", "")
    verification_code = payload.get("verificationCode", "")
    val, typ = extract_yt(url)
    if not val: return jsonify({"success": False, "error": "Bad YouTube URL"}), 400
    return jsonify(yt_lookup(val, typ, verification_code))

@app.route("/api/twitter", methods=["POST"])
def twitter_endpoint():
    payload = request.get_json() or {}
    url = payload.get("url", "")
    verification_code = payload.get("verificationCode", "")
    user = extract_tw(url)
    if not user: return jsonify({"success": False, "error": "Bad Twitter handle"}), 400
    return jsonify(scrape_twitter(user, verification_code))

@app.route("/api/instagram", methods=["POST"])
def instagram_endpoint():
    payload = request.get_json() or {}
    url = payload.get("url", "")
    verification_code = payload.get("verificationCode", "")
    user = extract_ig(url)
    if not user: return jsonify({"success": False, "error": "Bad Instagram handle"}), 400
    return jsonify(scrape_instagram(user, verification_code))

@app.route("/api/tiktok", methods=["POST"])
def tiktok_endpoint():
    payload = request.get_json() or {}
    url = payload.get("url", "")
    verification_code = payload.get("verificationCode", "")
    user = extract_tt(url)
    if not user: return jsonify({"success": False, "error": "Bad TikTok handle"}), 400
    return jsonify(scrape_tiktok(user, verification_code))

@app.route("/api/telegram", methods=["POST"])
def telegram_endpoint():
    payload = request.get_json() or {}
    url = payload.get("url", "")
    verification_code = payload.get("verificationCode", "")
    user = extract_tg(url)
    if not user: return jsonify({"success": False, "error": "Bad Telegram handle"}), 400
    return jsonify(scrape_telegram(user, verification_code))

@app.route("/api/facebook", methods=["POST"])
def facebook_endpoint():
    payload = request.get_json() or {}
    url = payload.get("url", "")
    verification_code = payload.get("verificationCode", "")
    ident, etype = extract_fb(url)
    if etype == "profile":
        return jsonify({"success": False, "error": "Personal Facebook profiles cannot be scraped!"}), 400
    if not ident: return jsonify({"success": False, "error": "Bad Facebook URL"}), 400
    return jsonify(scrape_facebook(ident, etype, verification_code))


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
        "version": "29.0 - FLEXIBLE LINKS + RETRIES",
        "endpoints": {
            "/": "GET – UI",
            "/api/scrape": "POST – universal",
            "/api/youtube": "POST – YouTube only",
            "/api/twitter": "POST – Twitter/X (syndication + Nitter fallback)",
            "/api/instagram": "POST – Instagram (API + 3-tier fallback)",
            "/api/tiktok": "POST – TikTok",
            "/api/telegram": "POST – Telegram",
            "/api/facebook": "POST – Facebook (Pages & Groups)",
            "/api/health": "GET – health check",
        },
        "fixes": {
            "twitter": "Added cdn.syndication.twimg.com widget API as primary method (works without login)",
            "instagram": "Added web_profile_info API on both i.instagram.com and www.instagram.com with dummy session cookie",
            "reliability": "Better error handling and multiple scraping strategies per platform"
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")), debug=os.getenv("FLASK_DEBUG") == "1")
