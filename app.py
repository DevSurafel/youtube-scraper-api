from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import re
import os

app = Flask(__name__)
CORS(app)

# API Keys
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

# Try to import ntscraper, fallback to manual scraping if not available
try:
    from ntscraper import Nitter
    NTSCRAPER_AVAILABLE = True
    # Initialize with working instance
    scraper = Nitter(log_level=1, skip_instance_check=False)
except ImportError:
    NTSCRAPER_AVAILABLE = False
    print("Warning: ntscraper not installed. Twitter scraping may be limited.")

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

def scrape_twitter_with_ntscraper(username):
    """Scrape Twitter using ntscraper library"""
    try:
        username = username.lstrip('@')
        
        # Get profile info
        profile = scraper.get_profile_info(username)
        
        if profile and 'username' in profile:
            return {
                'success': True,
                'username': profile.get('username', username),
                'name': profile.get('name', username),
                'follower_count': format_count(str(profile.get('followers', 0)), 'followers'),
                'follower_count_exact': str(profile.get('followers', 0)),
                'following_count': format_count(str(profile.get('following', 0)), 'following'),
                'tweet_count': format_count(str(profile.get('tweets', 0)), 'tweets'),
                'profile_url': f"https://twitter.com/{username}",
                'profile_image': profile.get('profile_pic_url', f'https://unavatar.io/twitter/{username}'),
                'bio': profile.get('bio', ''),
                'platform': 'twitter',
                'method': 'ntscraper'
            }
        
        return {
            'success': False,
            'error': f'Could not fetch data for @{username}'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Scraping failed: {str(e)}'
        }

def scrape_twitter_fallback(username):
    """Fallback Twitter scraping without ntscraper"""
    try:
        from bs4 import BeautifulSoup
        username = username.lstrip('@')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Try xcancel.com (most reliable currently)
        try:
            url = f'https://xcancel.com/{username}'
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                name_elem = soup.find('h2', class_='profile-card-fullname')
                name = name_elem.text.strip() if name_elem else username
                
                stats = soup.find_all('span', class_='profile-stat-num')
                followers = stats[2].text.strip() if len(stats) > 2 else '0'
                following = stats[1].text.strip() if len(stats) > 1 else '0'
                tweets = stats[0].text.strip() if len(stats) > 0 else '0'
                
                bio_elem = soup.find('p', class_='profile-bio')
                bio = bio_elem.text.strip() if bio_elem else ''
                
                def parse_count(text):
                    text = text.upper().replace(',', '')
                    if 'K' in text:
                        return int(float(text.replace('K', '')) * 1000)
                    elif 'M' in text:
                        return int(float(text.replace('M', '')) * 1000000)
                    return int(text) if text.isdigit() else 0
                
                follower_count = parse_count(followers)
                
                if follower_count > 0:
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
        
        return {
            'success': False,
            'error': f'Could not fetch data for @{username}. Please try again later.'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error: {str(e)}'
        }

def scrape_twitter_profile(username):
    """Main Twitter scraping function"""
    if NTSCRAPER_AVAILABLE:
        result = scrape_twitter_with_ntscraper(username)
        if result['success']:
            return result
    
    # Fallback to manual scraping
    return scrape_twitter_fallback(username)

# ==================== API ENDPOINTS ====================

@app.route('/api/scrape', methods=['POST'])
def scrape_social():
    """Universal endpoint"""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a URL'
        }), 400
    
    url = data['url'].strip()
    
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
            
    elif 'twitter.com' in url or 'x.com' in url or url.startswith('@'):
        username = extract_twitter_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid Twitter username'
            }), 400
        
        result = scrape_twitter_profile(username)
        
    else:
        return jsonify({
            'success': False,
            'error': 'Unsupported platform'
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

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'youtube_configured': bool(YOUTUBE_API_KEY),
        'ntscraper_available': NTSCRAPER_AVAILABLE,
        'twitter_method': 'ntscraper + fallback' if NTSCRAPER_AVAILABLE else 'manual scraping'
    }), 200

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'message': 'Social Media Stats API',
        'version': '4.0',
        'platforms': ['YouTube', 'Twitter/X'],
        'endpoints': {
            '/api/scrape': 'POST - Universal scraper',
            '/api/youtube': 'POST - YouTube',
            '/api/twitter': 'POST - Twitter',
            '/api/health': 'GET - Health check'
        },
        'status': {
            'youtube': 'active' if YOUTUBE_API_KEY else 'inactive',
            'twitter': 'active with ntscraper' if NTSCRAPER_AVAILABLE else 'active with fallback'
        }
    }), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
