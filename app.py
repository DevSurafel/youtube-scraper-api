from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import os
import json
import asyncio
from functools import wraps
import time

app = Flask(__name__)
CORS(app)

# API Keys
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
TELEGRAM_API_ID = os.environ.get('TELEGRAM_API_ID', '')
TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH', '')
TELEGRAM_SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING', '')

# Try to import Telethon for Telegram
TELEGRAM_AVAILABLE = False
try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.errors import UsernameNotOccupiedError, UsernameInvalidError, ChannelPrivateError
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

# ==================== TWITTER FUNCTIONS (ULTRA IMPROVED) ====================

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
    """Ultra improved Twitter profile scraping with multiple methods"""
    try:
        username = username.lstrip('@')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        
        # List of Nitter instances to try
        nitter_instances = [
            'https://xcancel.com',
            'https://nitter.poast.org',
            'https://nitter.net',
            'https://nitter.privacydev.net',
            'https://nitter.1d4.us',
            'https://nitter.cz',
            'https://bird.makeup',
            'https://nitter.it'
        ]
        
        for nitter_url in nitter_instances:
            try:
                url = f'{nitter_url}/{username}'
                response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
                
                if response.status_code == 200 and len(response.text) > 500:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Extract name
                    name_elem = (soup.find('a', class_='profile-card-fullname') or 
                                soup.find('div', class_='profile-card-fullname') or
                                soup.find('h2', class_='profile-card-fullname'))
                    name = name_elem.text.strip() if name_elem else username
                    
                    # Extract bio
                    bio_elem = soup.find('p', class_='profile-bio')
                    bio = bio_elem.text.strip() if bio_elem else ''
                    
                    # Extract stats
                    followers = '0'
                    following = '0'
                    tweets = '0'
                    
                    stats = soup.find_all('span', class_='profile-stat-num')
                    if len(stats) >= 3:
                        tweets = stats[0].text.strip()
                        following = stats[1].text.strip()
                        followers = stats[2].text.strip()
                    else:
                        # Alternative parsing
                        stat_container = soup.find('ul', class_='profile-statlist')
                        if stat_container:
                            for li in stat_container.find_all('li'):
                                label = li.find('span', class_='profile-stat-header')
                                value = li.find('span', class_='profile-stat-num')
                                if label and value:
                                    label_text = label.text.lower()
                                    if 'tweet' in label_text or 'post' in label_text:
                                        tweets = value.text.strip()
                                    elif 'following' in label_text:
                                        following = value.text.strip()
                                    elif 'follower' in label_text:
                                        followers = value.text.strip()
                    
                    follower_count = parse_count(followers)
                    
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
                    
                    # Accept if we found real data
                    if follower_count >= 0 and (name != username or follower_count > 0):
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
                            'method': nitter_url.replace('https://', '')
                        }
            except Exception as e:
                print(f"{nitter_url} failed: {e}")
                continue
        
        # Twitter Syndication API fallback
        try:
            api_url = f'https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={username}'
            response = requests.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    user_data = data[0]
                    return {
                        'success': True,
                        'username': username,
                        'name': user_data.get('name', username),
                        'follower_count': format_count(str(user_data.get('followers_count', 0)), 'followers'),
                        'follower_count_exact': str(user_data.get('followers_count', 0)),
                        'following_count': 'Hidden',
                        'tweet_count': 'Hidden',
                        'profile_url': f"https://twitter.com/{username}",
                        'profile_image': f'https://unavatar.io/twitter/{username}',
                        'bio': 'Bio unavailable via this method',
                        'platform': 'twitter',
                        'method': 'twitter_syndication_api'
                    }
        except Exception as e:
            print(f"Syndication API failed: {e}")
        
        # Unavatar fallback - check if profile exists
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
                    'bio': 'Profile exists but stats are temporarily unavailable.',
                    'platform': 'twitter',
                    'method': 'unavatar_fallback'
                }
        except Exception as e:
            print(f"Unavatar check failed: {e}")
        
        return {
            'success': False,
            'error': f'Could not fetch data for @{username}. The account may be private, suspended, or all scraping services are temporarily down.'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error: {str(e)}'
        }

# ==================== FACEBOOK FUNCTIONS ====================

def extract_facebook_identifier(url):
    """Extract username or ID from Facebook URL"""
    patterns = [
        r'facebook\.com/([a-zA-Z0-9.]+)',
        r'fb\.com/([a-zA-Z0-9.]+)',
        r'^@?([a-zA-Z0-9.]+)$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            identifier = match.group(1)
            if identifier.lower() not in ['profile.php', 'watch', 'groups', 'pages', 'marketplace']:
                return identifier
    return None

def scrape_facebook_profile(identifier):
    """Scrape Facebook profile/page information"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        # Try basic scraping
        try:
            url = f'https://www.facebook.com/{identifier}'
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Try to extract name from title
                title = soup.find('title')
                name = title.text.strip() if title else identifier
                name = name.replace(' | Facebook', '').replace(' - Facebook', '').strip()
                
                # Try to find follower/like count in the page
                text = response.text
                follower_match = re.search(r'([\d,]+)\s+(?:followers|likes|members)', text, re.IGNORECASE)
                
                followers = 'Hidden'
                if follower_match:
                    followers = format_count(follower_match.group(1).replace(',', ''), 'followers')
                
                return {
                    'success': True,
                    'username': identifier,
                    'name': name,
                    'follower_count': followers,
                    'profile_url': f"https://www.facebook.com/{identifier}",
                    'profile_image': f'https://unavatar.io/facebook/{identifier}',
                    'bio': 'Facebook scraping is limited due to authentication requirements',
                    'platform': 'facebook',
                    'method': 'facebook_basic',
                    'note': 'Facebook requires login for detailed stats. Only basic info available.'
                }
        except Exception as e:
            print(f"Facebook scraping failed: {e}")
        
        # Fallback - just confirm profile exists
        try:
            img_url = f'https://unavatar.io/facebook/{identifier}'
            response = requests.head(img_url, timeout=5)
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'username': identifier,
                    'name': identifier,
                    'follower_count': 'Hidden',
                    'profile_url': f"https://www.facebook.com/{identifier}",
                    'profile_image': img_url,
                    'bio': 'Facebook profile exists but detailed stats require authentication',
                    'platform': 'facebook',
                    'method': 'unavatar_fallback',
                    'note': 'Facebook requires login for stats'
                }
        except:
            pass
        
        return {
            'success': False,
            'error': f'Could not fetch Facebook profile for {identifier}. Facebook requires authentication for most data.'
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

# ==================== TELEGRAM FUNCTIONS (FIXED WITH SESSION STRING) ====================

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
    """Fixed Telegram scraping using session string"""
    if not TELEGRAM_AVAILABLE or not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        return {
            'success': False,
            'error': 'Telegram API not configured. Set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables.'
        }
    
    if not TELEGRAM_SESSION_STRING:
        return {
            'success': False,
            'error': 'Telegram session not configured. Set TELEGRAM_SESSION_STRING environment variable.'
        }
    
    try:
        username = username.lstrip('@')
        
        # Create client using session string
        session = StringSession(TELEGRAM_SESSION_STRING)
        client = TelegramClient(session, int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        
        await client.connect()
        
        # Check if session is valid
        if not await client.is_user_authorized():
            await client.disconnect()
            return {
                'success': False,
                'error': 'Telegram session is invalid or expired. Please generate a new session string.'
            }
        
        try:
            # Get entity (could be user, channel, or group)
            entity = await client.get_entity(username)
            
            # Check if it's a channel/group or user
            is_channel = hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup')
            
            profile_photo = f'https://unavatar.io/telegram/{username}'
            
            if is_channel:
                # It's a channel or group
                try:
                    full_channel = await client(GetFullChannelRequest(entity))
                    
                    name = entity.title
                    member_count = full_channel.full_chat.participants_count
                    description = full_channel.full_chat.about or ''
                    
                    # Determine type
                    if entity.broadcast:
                        entity_type = 'Channel'
                    elif entity.megagroup:
                        entity_type = 'Supergroup'
                    else:
                        entity_type = 'Group'
                    
                    await client.disconnect()
                    
                    return {
                        'success': True,
                        'username': entity.username or username,
                        'name': name,
                        'type': entity_type,
                        'member_count': format_count(str(member_count), 'members'),
                        'member_count_exact': str(member_count),
                        'profile_url': f"https://t.me/{username}",
                        'profile_image': profile_photo,
                        'description': description,
                        'is_verified': entity.verified if hasattr(entity, 'verified') else False,
                        'platform': 'telegram',
                        'method': 'telethon_api'
                    }
                except ChannelPrivateError:
                    await client.disconnect()
                    return {
                        'success': False,
                        'error': f'Channel/Group @{username} is private and cannot be accessed'
                    }
            else:
                # It's a user
                full_user = await client(GetFullUserRequest(entity))
                
                name = f"{entity.first_name or ''} {entity.last_name or ''}".strip() or username
                bio = full_user.full_user.about or ''
                
                await client.disconnect()
                
                return {
                    'success': True,
                    'username': entity.username or username,
                    'name': name,
                    'type': 'User',
                    'user_id': entity.id,
                    'profile_url': f"https://t.me/{username}",
                    'profile_image': profile_photo,
                    'bio': bio,
                    'is_bot': entity.bot,
                    'is_verified': entity.verified if hasattr(entity, 'verified') else False,
                    'platform': 'telegram',
                    'method': 'telethon_api',
                    'note': 'Telegram users do not have public follower counts'
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
                'error': f'Error fetching Telegram entity: {str(e)}'
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
        
    elif 'facebook.com' in url or 'fb.com' in url:
        identifier = extract_facebook_identifier(url)
        if not identifier:
            return jsonify({
                'success': False,
                'error': 'Invalid Facebook URL'
            }), 400
        
        result = scrape_facebook_profile(identifier)
        
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
        'version': '8.0',
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
            'twitter': 'active (8 methods)',
            'tiktok': 'active',
            'telegram': 'active (channels + users)' if (TELEGRAM_API_ID and TELEGRAM_API_HASH) else 'inactive'
        }
    }), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
