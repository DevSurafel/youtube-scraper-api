from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import re
import os

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend requests

# Get API key from environment variable (set in Render dashboard)
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

def extract_channel_identifier(url):
    """
    Extract channel ID or username from various YouTube URL formats
    Supports:
    - youtube.com/channel/UC...
    - youtube.com/@username
    - youtube.com/c/channelname
    - youtube.com/user/username
    """
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

def format_subscriber_count(count_str):
    """Format subscriber count in readable format"""
    try:
        count = int(count_str)
        
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B subscribers"
        elif count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M subscribers"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K subscribers"
        else:
            return f"{count} subscribers"
    except:
        return "Subscriber count hidden"

def get_channel_by_id(channel_id):
    """Get channel information using channel ID"""
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
                'subscriber_count': format_subscriber_count(
                    channel['statistics'].get('subscriberCount', '0')
                ),
                'subscriber_count_exact': channel['statistics'].get('subscriberCount', '0'),
                'channel_url': f"https://www.youtube.com/channel/{channel_id}",
                'thumbnail': channel['snippet']['thumbnails']['high']['url']
            }
        
        return {
            'success': False,
            'error': 'Channel not found with the provided ID'
        }
        
    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': f'Network error: {str(e)}'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Error: {str(e)}'
        }

def search_channel_by_handle(handle):
    """Search for channel using handle (@username) or custom URL"""
    try:
        # Remove @ if present
        handle = handle.lstrip('@')
        
        # Search for the channel
        search_url = 'https://www.googleapis.com/youtube/v3/search'
        search_params = {
            'part': 'snippet',
            'q': handle,
            'type': 'channel',
            'key': YOUTUBE_API_KEY,
            'maxResults': 5
        }
        
        response = requests.get(search_url, params=search_params, timeout=10)
        data = response.json()
        
        if 'error' in data:
            return {
                'success': False,
                'error': f"API Error: {data['error'].get('message', 'Unknown error')}"
            }
        
        if 'items' in data and len(data['items']) > 0:
            # Get the first result's channel ID
            channel_id = data['items'][0]['snippet']['channelId']
            # Now get full channel info
            return get_channel_by_id(channel_id)
        
        return {
            'success': False,
            'error': f'No channel found with handle: @{handle}'
        }
        
    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': f'Network error: {str(e)}'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Error: {str(e)}'
        }

@app.route('/api/scrape', methods=['POST'])
def scrape_channel():
    """
    API endpoint to get YouTube channel information
    Expects JSON: {"url": "youtube_channel_url"}
    Returns: channel name, subscriber count, and other info
    """
    
    # Check if API key is configured
    if not YOUTUBE_API_KEY:
        return jsonify({
            'success': False,
            'error': 'YouTube API key not configured. Please set YOUTUBE_API_KEY environment variable.'
        }), 500
    
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a YouTube channel URL'
        }), 400
    
    channel_url = data['url'].strip()
    
    # Validate YouTube URL
    youtube_pattern = r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/(channel/|c/|user/|@)?[\w-]+'
    if not re.match(youtube_pattern, channel_url):
        return jsonify({
            'success': False,
            'error': 'Invalid YouTube URL. Please provide a valid YouTube channel URL.'
        }), 400
    
    # Extract channel identifier
    identifier, id_type = extract_channel_identifier(channel_url)
    
    if not identifier:
        return jsonify({
            'success': False,
            'error': 'Could not extract channel information from URL'
        }), 400
    
    # Get channel information based on identifier type
    if id_type == 'id':
        # Direct channel ID
        result = get_channel_by_id(identifier)
    else:
        # Handle, custom URL, or username - need to search
        result = search_channel_by_handle(identifier)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify API is running"""
    api_configured = bool(YOUTUBE_API_KEY)
    return jsonify({
        'status': 'healthy',
        'api_key_configured': api_configured
    }), 200

@app.route('/', methods=['GET'])
def home():
    """Root endpoint with API information"""
    return jsonify({
        'message': 'YouTube Channel Scraper API',
        'version': '2.0',
        'endpoints': {
            '/api/health': 'GET - Health check',
            '/api/scrape': 'POST - Scrape channel info (requires {"url": "channel_url"})'
        },
        'api_key_configured': bool(YOUTUBE_API_KEY)
    }), 200

if __name__ == '__main__':
    # For local development
    app.run(debug=True, host='0.0.0.0', port=5001)
