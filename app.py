from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import re
import os

app = Flask(__name__)
CORS(app)

# API Keys from environment variables
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
TWITTER_BEARER_TOKEN = os.environ.get('TWITTER_BEARER_TOKEN', '')

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
            return f"{count} {suffix}"
    except:
        return f"Count hidden"

def get_youtube_channel_by_id(channel_id):
    """Get YouTube channel information using channel ID"""
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
    """Search for YouTube channel using handle"""
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
        r'^@?([a-zA-Z0-9_]+)$'  # Just username
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_twitter_user_info(username):
    """Get Twitter user information using Twitter API v2"""
    try:
        if not TWITTER_BEARER_TOKEN:
            return {
                'success': False,
                'error': 'Twitter API not configured. Please set TWITTER_BEARER_TOKEN.'
            }
        
        # Remove @ if present
        username = username.lstrip('@')
        
        # Twitter API v2 endpoint
        url = f'https://api.twitter.com/2/users/by/username/{username}'
        params = {
            'user.fields': 'public_metrics,profile_image_url,description'
        }
        headers = {
            'Authorization': f'Bearer {TWITTER_BEARER_TOKEN}'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 401:
            return {
                'success': False,
                'error': 'Invalid Twitter API credentials. Please check TWITTER_BEARER_TOKEN.'
            }
        
        if response.status_code == 429:
            return {
                'success': False,
                'error': 'Twitter API rate limit exceeded. Please try again later.'
            }
        
        data = response.json()
        
        if 'data' in data:
            user = data['data']
            metrics = user.get('public_metrics', {})
            
            return {
                'success': True,
                'username': user['username'],
                'name': user['name'],
                'follower_count': format_count(
                    str(metrics.get('followers_count', 0)),
                    'followers'
                ),
                'follower_count_exact': str(metrics.get('followers_count', 0)),
                'following_count': format_count(
                    str(metrics.get('following_count', 0)),
                    'following'
                ),
                'tweet_count': format_count(
                    str(metrics.get('tweet_count', 0)),
                    'tweets'
                ),
                'profile_url': f"https://twitter.com/{user['username']}",
                'profile_image': user.get('profile_image_url', '').replace('_normal', '_400x400'),
                'bio': user.get('description', ''),
                'platform': 'twitter'
            }
        
        if 'errors' in data:
            error_msg = data['errors'][0].get('detail', 'User not found')
            return {'success': False, 'error': error_msg}
        
        return {'success': False, 'error': 'User not found'}
        
    except Exception as e:
        return {'success': False, 'error': f'Error: {str(e)}'}

# ==================== API ENDPOINTS ====================

@app.route('/api/scrape', methods=['POST'])
def scrape_social():
    """Universal endpoint for both YouTube and Twitter"""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a URL'
        }), 400
    
    url = data['url'].strip()
    
    # Detect platform
    if 'youtube.com' in url or 'youtu.be' in url:
        # YouTube handling
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
        # Twitter handling
        username = extract_twitter_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid Twitter username or URL'
            }), 400
        
        result = get_twitter_user_info(username)
        
    else:
        return jsonify({
            'success': False,
            'error': 'Unsupported platform. Please provide YouTube or Twitter/X URL.'
        }), 400
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@app.route('/api/youtube', methods=['POST'])
def scrape_youtube():
    """YouTube-specific endpoint"""
    if not YOUTUBE_API_KEY:
        return jsonify({
            'success': False,
            'error': 'YouTube API not configured'
        }), 500
    
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a YouTube channel URL'
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
    """Twitter-specific endpoint"""
    if not TWITTER_BEARER_TOKEN:
        return jsonify({
            'success': False,
            'error': 'Twitter API not configured'
        }), 500
    
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a Twitter username or URL'
        }), 400
    
    url = data['url'].strip()
    username = extract_twitter_username(url)
    
    if not username:
        return jsonify({
            'success': False,
            'error': 'Invalid Twitter username or URL'
        }), 400
    
    result = get_twitter_user_info(username)
    return jsonify(result), 200 if result['success'] else 404

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'youtube_configured': bool(YOUTUBE_API_KEY),
        'twitter_configured': bool(TWITTER_BEARER_TOKEN)
    }), 200

@app.route('/', methods=['GET'])
def home():
    """Root endpoint with API information"""
    return jsonify({
        'message': 'Social Media Info API',
        'version': '2.0',
        'supported_platforms': ['YouTube', 'Twitter/X'],
        'endpoints': {
            '/api/health': 'GET - Health check',
            '/api/scrape': 'POST - Universal scraper (auto-detects platform)',
            '/api/youtube': 'POST - YouTube-specific endpoint',
            '/api/twitter': 'POST - Twitter-specific endpoint'
        },
        'youtube_configured': bool(YOUTUBE_API_KEY),
        'twitter_configured': bool(TWITTER_BEARER_TOKEN)
    }), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
