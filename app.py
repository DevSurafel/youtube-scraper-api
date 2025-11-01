from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import os
import json

app = Flask(__name__)
CORS(app)

# API Keys
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

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
    """Scrape Twitter profile using multiple reliable methods"""
    try:
        username = username.lstrip('@')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        # Method 1: Try xcancel.com
        try:
            url = f'https://xcancel.com/{username}'
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200 and len(response.text) > 500:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract name
                name_elem = soup.find('h2', class_='profile-card-fullname') or soup.find('a', class_='profile-card-fullname')
                name = name_elem.text.strip() if name_elem else username
                
                # Extract stats
                stats = soup.find_all('span', class_='profile-stat-num')
                
                if len(stats) >= 3:
                    tweets = stats[0].text.strip()
                    following = stats[1].text.strip()
                    followers = stats[2].text.strip()
                    
                    follower_count = parse_count(followers)
                    
                    if follower_count > 0:
                        bio_elem = soup.find('p', class_='profile-bio')
                        bio = bio_elem.text.strip() if bio_elem else ''
                        
                        return {
                            'success': True,
                            'username': username,
                            'name': name,
                            'follower_count': format_count(str(follower_count), 'followers'),
                            'follower_count_exact': str(follower_count),
                            'following_count': format_count(str(parse_count(following)), 'following'),
                            'tweet_count': format_count(str(parse_count(tweets)), 'tweets'),
                            'profile_url': f"https://twitter.com/{username}",
                            'profile_image': f'https://unavatar.io/twitter/{username}',
                            'bio': bio,
                            'platform': 'twitter',
                            'method': 'xcancel'
                        }
        except Exception as e:
            print(f"xcancel failed: {e}")
        
        # Method 2: Try nitter.poast.org
        try:
            url = f'https://nitter.poast.org/{username}'
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                name_elem = soup.find('a', class_='profile-card-fullname')
                name = name_elem.text.strip() if name_elem else username
                
                stats = soup.find_all('span', class_='profile-stat-num')
                
                if len(stats) >= 3:
                    tweets = stats[0].text.strip()
                    following = stats[1].text.strip()
                    followers = stats[2].text.strip()
                    
                    follower_count = parse_count(followers)
                    
                    if follower_count > 0:
                        bio_elem = soup.find('p', class_='profile-bio')
                        bio = bio_elem.text.strip() if bio_elem else ''
                        
                        return {
                            'success': True,
                            'username': username,
                            'name': name,
                            'follower_count': format_count(str(follower_count), 'followers'),
                            'follower_count_exact': str(follower_count),
                            'following_count': format_count(str(parse_count(following)), 'following'),
                            'tweet_count': format_count(str(parse_count(tweets)), 'tweets'),
                            'profile_url': f"https://twitter.com/{username}",
                            'profile_image': f'https://unavatar.io/twitter/{username}',
                            'bio': bio,
                            'platform': 'twitter',
                            'method': 'nitter.poast'
                        }
        except Exception as e:
            print(f"nitter.poast failed: {e}")
        
        # Method 3: Use unavatar API for basic info
        try:
            # This gives us at least the profile exists confirmation
            img_url = f'https://unavatar.io/twitter/{username}'
            response = requests.head(img_url, timeout=5)
            
            if response.status_code == 200:
                # Profile exists, return minimal info
                return {
                    'success': True,
                    'username': username,
                    'name': username,
                    'follower_count': 'Hidden',
                    'follower_count_exact': '0',
                    'following_count': 'Hidden',
                    'tweet_count': 'Hidden',
                    'profile_url': f"https://twitter.com/{username}",
                    'profile_image': img_url,
                    'bio': 'Stats temporarily unavailable',
                    'platform': 'twitter',
                    'method': 'unavatar (limited data)'
                }
        except:
            pass
        
        return {
            'success': False,
            'error': f'Could not fetch data for @{username}. The account may be private, suspended, or scraping methods are currently unavailable.'
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
                # TikTok loads data via JavaScript, so we look for JSON in script tags
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for JSON data in script tags
                scripts = soup.find_all('script', {'id': '__UNIVERSAL_DATA_FOR_REHYDRATION__'})
                
                for script in scripts:
                    try:
                        data = json.loads(script.string)
                        
                        # Navigate through TikTok's data structure
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
        
        # Method 2: Try TikTok API proxy
        try:
            api_url = f'https://www.tiktok.com/api/user/detail/?uniqueId={username}'
            response = requests.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('statusCode') == 0 and 'userInfo' in data:
                    user = data['userInfo']['user']
                    stats = data['userInfo']['stats']
                    
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
                        'method': 'tiktok_api'
                    }
        except Exception as e:
            print(f"TikTok API failed: {e}")
        
        # Method 3: Return basic profile confirmation
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

# ==================== API ENDPOINTS ====================

@app.route('/api/scrape', methods=['POST'])
def scrape_social():
    """Universal endpoint for YouTube, Twitter, and TikTok"""
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
            
    elif 'twitter.com' in url or 'x.com' in url or (url.startswith('@') and 'tiktok' not in url.lower()):
        username = extract_twitter_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid Twitter username'
            }), 400
        
        result = scrape_twitter_profile(username)
        
    elif 'tiktok.com' in url or (url.startswith('@') and 'tiktok' in url.lower()):
        username = extract_tiktok_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid TikTok username'
            }), 400
        
        result = scrape_tiktok_profile(username)
        
    else:
        return jsonify({
            'success': False,
            'error': 'Unsupported platform. Please provide YouTube, Twitter, or TikTok URL.'
        }), 400
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@app.route('/api/youtube', methods=['POST'])
def scrape_youtube():
    """YouTube endpoint"""
    if not YOUTUBE_API_KEY:
        return jsonify({
            'success': False,
            'error': 'YouTube API not configured'
        }), 500
    
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a YouTube URL'
        }), 400
    
    url = data['url'].strip()
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
    
    return jsonify(result), 200 if result['success'] else 404

@app.route('/api/twitter', methods=['POST'])
def scrape_twitter():
    """Twitter endpoint"""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a Twitter username'
        }), 400
    
    url = data['url'].strip()
    username = extract_twitter_username(url)
    
    if not username:
        return jsonify({
            'success': False,
            'error': 'Invalid Twitter username'
        }), 400
    
    result = scrape_twitter_profile(username)
    return jsonify(result), 200 if result['success'] else 404

@app.route('/api/tiktok', methods=['POST'])
def scrape_tiktok():
    """TikTok endpoint"""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a TikTok username'
        }), 400
    
    url = data['url'].strip()
    username = extract_tiktok_username(url)
    
    if not username:
        return jsonify({
            'success': False,
            'error': 'Invalid TikTok username'
        }), 400
    
    result = scrape_tiktok_profile(username)
    return jsonify(result), 200 if result['success'] else 404

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'youtube_configured': bool(YOUTUBE_API_KEY),
        'supported_platforms': ['YouTube', 'Twitter/X', 'TikTok']
    }), 200

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'message': 'Social Media Stats API',
        'version': '5.0',
        'platforms': ['YouTube', 'Twitter/X', 'TikTok'],
        'endpoints': {
            '/api/scrape': 'POST - Universal scraper',
            '/api/youtube': 'POST - YouTube',
            '/api/twitter': 'POST - Twitter',
            '/api/tiktok': 'POST - TikTok',
            '/api/health': 'GET - Health check'
        },
        'status': {
            'youtube': 'active' if YOUTUBE_API_KEY else 'inactive',
            'twitter': 'active',
            'tiktok': 'active'
        }
    }), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
