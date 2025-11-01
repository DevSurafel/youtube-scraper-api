from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import os
import json
import asyncio
from functools import wraps

app = Flask(__name__)
CORS(app)

# API Keys
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
TELEGRAM_API_ID = os.environ.get('TELEGRAM_API_ID', '')
TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH', '')

# Try to import Telethon for Telegram
TELEGRAM_AVAILABLE = False
try:
    from telethon.sync import TelegramClient
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.errors import UsernameNotOccupiedError, UsernameInvalidError
    TELEGRAM_AVAILABLE = True
except ImportError:
    print("Warning: Telethon not installed. Telegram scraping will be limited.")

# ==================== ASYNC HELPER ====================

def async_route(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper

# ==================== YOUTUBE FUNCTIONS ====================

def extract_channel_identifier(url):
    """Extract channel ID or username from YouTube URL"""
    patterns = [
        (r'youtube\.com/channel/(UC[\w-]+)', 'id'),
        (r'youtube\.com/@([\w-]+)', 'handle'),
        (r'youtube\.com/c/([\w-]+)', 'custom'),
        (r'youtube\.com/user/([\w-]+)', 'user')
    ]
    
    for pattern, id_type in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), id_type
    return None, None

def format_count(count_str, suffix="subscribers"):
    """Format count in readable format"""
    try:
        count = int(count_str)
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B {suffix}"
        elif count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M {suffix}"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K {suffix}"
        else:
            return f"{count:,} {suffix}"
    except:
        return f"Count hidden"

def get_youtube_channel_by_id(channel_id):
    """Get YouTube channel information"""
    try:
        url = 'https://www.googleapis.com/youtube/v3/channels'
        params = {
            'part': 'snippet,statistics',
            'id': channel_id,
            'key': YOUTUBE_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'error' in data:
            return {
                'success': False,
                'error': f"API Error: {data['error'].get('message', 'Unknown error')}"
            }
        
        if 'items' in data and len(data['items']) > 0:
            channel = data['items'][0]
            return {
                'success': True,
                'channel_name': channel['snippet']['title'],
                'subscriber_count': format_count(
                    channel['statistics'].get('subscriberCount', '0'),
                    'subscribers'
                ),
                'subscriber_count_exact': channel['statistics'].get('subscriberCount', '0'),
                'channel_url': f"https://www.youtube.com/channel/{channel_id}",
                'thumbnail': channel['snippet']['thumbnails']['high']['url'],
                'platform': 'youtube'
            }
        
        return {'success': False, 'error': 'Channel not found'}
        
    except Exception as e:
        return {'success': False, 'error': f'Error: {str(e)}'}

def search_youtube_channel(handle):
    """Search for YouTube channel"""
    try:
        handle = handle.lstrip('@')
        search_url = 'https://www.googleapis.com/youtube/v3/search'
        params = {
            'part': 'snippet',
            'q': handle,
            'type': 'channel',
            'key': YOUTUBE_API_KEY,
            'maxResults': 5
        }
        
        response = requests.get(search_url, params=params, timeout=10)
        data = response.json()
        
        if 'items' in data and len(data['items']) > 0:
            channel_id = data['items'][0]['snippet']['channelId']
            return get_youtube_channel_by_id(channel_id)
        
        return {'success': False, 'error': 'Channel not found'}
        
    except Exception as e:
        return {'success': False, 'error': f'Error: {str(e)}'}

# ==================== TWITTER FUNCTIONS ====================

def extract_twitter_username(url):
    """Extract username from Twitter/X URL"""
    patterns = [
        r'twitter\.com/([a-zA-Z0-9_]+)',
        r'x\.com/([a-zA-Z0-9_]+)',
        r'^@?([a-zA-Z0-9_]+)$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username = match.group(1)
            if username.lower() not in ['home', 'explore', 'notifications', 'messages', 'i', 'search', 'settings']:
                return username
    return None

def parse_count(text):
    """Parse follower count text to number"""
    try:
        text = str(text).strip().upper().replace(',', '').replace(' ', '')
        if 'K' in text:
            return int(float(text.replace('K', '')) * 1000)
        elif 'M' in text:
            return int(float(text.replace('M', '')) * 1000000)
        elif 'B' in text:
            return int(float(text.replace('B', '')) * 1000000000)
        return int(float(text)) if text.replace('.', '').isdigit() else 0
    except:
        return 0

def scrape_twitter_profile(username):
    """Scrape Twitter profile using multiple methods with better reliability"""
    try:
        username = username.lstrip('@')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        # Method 1: Try multiple Nitter instances with better parsing
        nitter_instances = [
            'https://nitter.poast.org',
            'https://nitter.privacydev.net',
            'https://xcancel.com'
        ]
        
        for nitter_url in nitter_instances:
            try:
                url = f'{nitter_url}/{username}'
                response = requests.get(url, headers=headers, timeout=15)
                
                if response.status_code == 200 and len(response.text) > 500:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Try multiple name selectors
                    name_elem = (soup.find('h2', class_='profile-card-fullname') or 
                                soup.find('a', class_='profile-card-fullname') or
                                soup.find('div', class_='profile-card-fullname'))
                    name = name_elem.text.strip() if name_elem else username
                    
                    # Try to find stats container
                    stats_container = soup.find('ul', class_='profile-statlist')
                    
                    followers = '0'
                    following = '0'
                    tweets = '0'
                    
                    if stats_container:
                        # Parse stats from list
                        stat_items = stats_container.find_all('li', class_='profile-stat')
                        for item in stat_items:
                            header = item.find('span', class_='profile-stat-header')
                            num = item.find('span', class_='profile-stat-num')
                            if header and num:
                                label = header.text.lower()
                                if 'tweet' in label or 'post' in label:
                                    tweets = num.text.strip()
                                elif 'following' in label:
                                    following = num.text.strip()
                                elif 'follower' in label:
                                    followers = num.text.strip()
                    else:
                        # Fallback: find all stat numbers
                        stats = soup.find_all('span', class_='profile-stat-num')
                        if len(stats) >= 3:
                            tweets = stats[0].text.strip()
                            following = stats[1].text.strip()
                            followers = stats[2].text.strip()
                    
                    follower_count = parse_count(followers)
                    
                    # Accept profile even with 0 followers if we found the page
                    if name != username or follower_count >= 0:
                        bio_elem = soup.find('p', class_='profile-bio')
                        bio = bio_elem.text.strip() if bio_elem else ''
                        
                        # Get profile image
                        img_elem = soup.find('a', class_='profile-card-avatar')
                        profile_image = f'https://unavatar.io/twitter/{username}'
                        if img_elem:
                            img_tag = img_elem.find('img')
                            if img_tag and img_tag.get('src'):
                                img_src = img_tag['src']
                                if img_src.startswith('http'):
                                    profile_image = img_src
                                elif img_src.startswith('/'):
                                    profile_image = nitter_url + img_src
                        
                        return {
                            'success': True,
                            'username': username,
                            'name': name,
                            'follower_count': format_count(str(follower_count), 'followers') if follower_count > 0 else 'Hidden',
                            'follower_count_exact': str(follower_count),
                            'following_count': format_count(str(parse_count(following)), 'following'),
                            'tweet_count': format_count(str(parse_count(tweets)), 'tweets'),
                            'profile_url': f"https://twitter.com/{username}",
                            'profile_image': profile_image,
                            'bio': bio,
                            'platform': 'twitter',
                            'method': f'nitter ({nitter_url})'
                        }
            except Exception as e:
                print(f"{nitter_url} failed: {e}")
                continue
        
        # Method 2: Check if profile exists via unavatar
        try:
            img_url = f'https://unavatar.io/twitter/{username}'
            response = requests.head(img_url, timeout=5)
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'username': username,
                    'name': username,
                    'follower_count': 'Stats Hidden',
                    'follower_count_exact': '0',
                    'following_count': 'Hidden',
                    'tweet_count': 'Hidden',
                    'profile_url': f"https://twitter.com/{username}",
                    'profile_image': img_url,
                    'bio': 'Profile exists but stats temporarily unavailable. This may be due to Twitter\'s anti-scraping measures.',
                    'platform': 'twitter',
                    'method': 'unavatar (profile confirmed)'
                }
        except:
            pass
        
        return {
            'success': False,
            'error': f'Could not fetch data for @{username}. The account may be private, suspended, or temporarily unavailable.'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error: {str(e)}'
        }

# ==================== TIKTOK FUNCTIONS ====================

def extract_tiktok_username(url):
    """Extract username from TikTok URL"""
    patterns = [
        r'tiktok\.com/@([a-zA-Z0-9_.]+)',
        r'^@?([a-zA-Z0-9_.]+)$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username = match.group(1)
            return username
    return None

def scrape_tiktok_profile(username):
    """Scrape TikTok profile information"""
    try:
        username = username.lstrip('@')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        # Method 1: Try TikTok web scraping
        try:
            url = f'https://www.tiktok.com/@{username}'
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                scripts = soup.find_all('script', {'id': '__UNIVERSAL_DATA_FOR_REHYDRATION__'})
                
                for script in scripts:
                    try:
                        data = json.loads(script.string)
                        user_detail = data.get('__DEFAULT_SCOPE__', {}).get('webapp.user-detail', {}).get('userInfo', {})
                        
                        if user_detail and 'user' in user_detail:
                            user = user_detail['user']
                            stats = user_detail.get('stats', {})
                            
                            return {
                                'success': True,
                                'username': user.get('uniqueId', username),
                                'name': user.get('nickname', username),
                                'follower_count': format_count(str(stats.get('followerCount', 0)), 'followers'),
                                'follower_count_exact': str(stats.get('followerCount', 0)),
                                'following_count': format_count(str(stats.get('followingCount', 0)), 'following'),
                                'video_count': format_count(str(stats.get('videoCount', 0)), 'videos'),
                                'likes_count': format_count(str(stats.get('heartCount', 0)), 'likes'),
                                'profile_url': f"https://www.tiktok.com/@{username}",
                                'profile_image': user.get('avatarLarger', f'https://unavatar.io/tiktok/{username}'),
                                'bio': user.get('signature', ''),
                                'platform': 'tiktok',
                                'method': 'tiktok_web'
                            }
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"TikTok web scraping failed: {e}")
        
        # Method 2: Profile exists check
        try:
            img_url = f'https://unavatar.io/tiktok/{username}'
            response = requests.head(img_url, timeout=5)
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'username': username,
                    'name': username,
                    'follower_count': 'Hidden',
                    'follower_count_exact': '0',
                    'following_count': 'Hidden',
                    'video_count': 'Hidden',
                    'likes_count': 'Hidden',
                    'profile_url': f"https://www.tiktok.com/@{username}",
                    'profile_image': img_url,
                    'bio': 'Stats temporarily unavailable',
                    'platform': 'tiktok',
                    'method': 'unavatar (limited data)'
                }
        except:
            pass
        
        return {
            'success': False,
            'error': f'Could not fetch data for @{username}. The account may be private or unavailable.'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error: {str(e)}'
        }

# ==================== TELEGRAM FUNCTIONS ====================

def extract_telegram_username(url):
    """Extract username from Telegram URL"""
    patterns = [
        r't\.me/([a-zA-Z0-9_]+)',
        r'telegram\.me/([a-zA-Z0-9_]+)',
        r'^@?([a-zA-Z0-9_]+)$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username = match.group(1)
            return username
    return None

async def scrape_telegram_profile_async(username):
    """Scrape Telegram profile using Telethon"""
    if not TELEGRAM_AVAILABLE or not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        return {
            'success': False,
            'error': 'Telegram API not configured. Please set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables.'
        }
    
    try:
        username = username.lstrip('@')
        
        # Create session
        client = TelegramClient('anon', int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        
        await client.connect()
        
        if not await client.is_user_authorized():
            # For bot mode, you don't need authorization
            pass
        
        try:
            # Get user info
            user = await client.get_entity(username)
            full_user = await client(GetFullUserRequest(user.id))
            
            # Get profile photo
            profile_photo = None
            if user.photo:
                try:
                    photo = await client.download_profile_photo(user, bytes)
                    # Convert to base64 or use unavatar
                    profile_photo = f'https://unavatar.io/telegram/{username}'
                except:
                    profile_photo = f'https://unavatar.io/telegram/{username}'
            else:
                profile_photo = f'https://unavatar.io/telegram/{username}'
            
            # Extract info
            name = f"{user.first_name or ''} {user.last_name or ''}".strip() or username
            bio = full_user.full_user.about or ''
            
            # Telegram doesn't provide follower counts for users
            # Only channels/groups have member counts
            
            await client.disconnect()
            
            return {
                'success': True,
                'username': user.username or username,
                'name': name,
                'user_id': user.id,
                'profile_url': f"https://t.me/{username}",
                'profile_image': profile_photo,
                'bio': bio,
                'is_bot': user.bot,
                'is_verified': user.verified,
                'platform': 'telegram',
                'method': 'telethon_api',
                'note': 'Telegram does not provide follower counts for users'
            }
            
        except UsernameNotOccupiedError:
            await client.disconnect()
            return {
                'success': False,
                'error': f'Username @{username} not found on Telegram'
            }
        except UsernameInvalidError:
            await client.disconnect()
            return {
                'success': False,
                'error': f'Invalid Telegram username'
            }
        except Exception as e:
            await client.disconnect()
            return {
                'success': False,
                'error': f'Error fetching Telegram user: {str(e)}'
            }
            
    except Exception as e:
        return {
            'success': False,
            'error': f'Telegram API error: {str(e)}'
        }

def scrape_telegram_profile(username):
    """Synchronous wrapper for Telegram scraping"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(scrape_telegram_profile_async(username))
        loop.close()
        return result
    except Exception as e:
        return {
            'success': False,
            'error': f'Error: {str(e)}'
        }

# ==================== API ENDPOINTS ====================

@app.route('/api/scrape', methods=['POST'])
def scrape_social():
    """Universal endpoint for all platforms"""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a URL'
        }), 400
    
    url = data['url'].strip()
    
    # Detect platform
    if 'youtube.com' in url or 'youtu.be' in url:
        if not YOUTUBE_API_KEY:
            return jsonify({
                'success': False,
                'error': 'YouTube API not configured'
            }), 500
        
        identifier, id_type = extract_channel_identifier(url)
        if not identifier:
            return jsonify({
                'success': False,
                'error': 'Invalid YouTube URL'
            }), 400
        
        if id_type == 'id':
            result = get_youtube_channel_by_id(identifier)
        else:
            result = search_youtube_channel(identifier)
            
    elif 'twitter.com' in url or 'x.com' in url:
        username = extract_twitter_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid Twitter username'
            }), 400
        
        result = scrape_twitter_profile(username)
        
    elif 'tiktok.com' in url:
        username = extract_tiktok_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid TikTok username'
            }), 400
        
        result = scrape_tiktok_profile(username)
        
    elif 't.me' in url or 'telegram.me' in url:
        username = extract_telegram_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid Telegram username'
            }), 400
        
        result = scrape_telegram_profile(username)
        
    elif url.startswith('@'):
        # Auto-detect for @username format
        # Try Twitter first, then TikTok, then Telegram
        username = url.lstrip('@')
        
        result = scrape_twitter_profile(username)
        if not result['success']:
            result = scrape_tiktok_profile(username)
            if not result['success']:
                result = scrape_telegram_profile(username)
        
    else:
        return jsonify({
            'success': False,
            'error': 'Unsupported platform. Please provide YouTube, Twitter, TikTok, or Telegram URL.'
        }), 400
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@app.route('/api/telegram', methods=['POST'])
def scrape_telegram():
    """Telegram endpoint"""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a Telegram username'
        }), 400
    
    url = data['url'].strip()
    username = extract_telegram_username(url)
    
    if not username:
        return jsonify({
            'success': False,
            'error': 'Invalid Telegram username'
        }), 400
    
    result = scrape_telegram_profile(username)
    return jsonify(result), 200 if result['success'] else 404

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'youtube_configured': bool(YOUTUBE_API_KEY),
        'telegram_configured': bool(TELEGRAM_API_ID and TELEGRAM_API_HASH),
        'supported_platforms': ['YouTube', 'Twitter/X', 'TikTok', 'Telegram']
    }), 200

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'message': 'Social Media Stats API',
        'version': '6.0',
        'platforms': ['YouTube', 'Twitter/X', 'TikTok', 'Telegram'],
        'endpoints': {
            '/api/scrape': 'POST - Universal scraper',
            '/api/youtube': 'POST - YouTube',
            '/api/twitter': 'POST - Twitter',
            '/api/tiktok': 'POST - TikTok',
            '/api/telegram': 'POST - Telegram',
            '/api/health': 'GET - Health check'
        },
        'status': {
            'youtube': 'active' if YOUTUBE_API_KEY else 'inactive',
            'twitter': 'active',
            'tiktok': 'active',
            'telegram': 'active' if (TELEGRAM_API_ID and TELEGRAM_API_HASH) else 'inactive'
        }
    }), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
